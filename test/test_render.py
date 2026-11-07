from pathlib import Path

from media_provider import main as provider_main
from media_render import Spotifice
from media_render import main as render_main
from metadata import identify

from gst_player import GstPlayer

from .icetest import IceTestCase

MEDIA = Path('test/media')
PLAYLISTS = Path('test/playlists')


class TestRender(IceTestCase):
    provider_port = 10000
    provider_identity = 'mediaProvider0'
    render_port = 10001
    render_identity = 'mediaRender0'

    def setUp(self):
        provider_props = {
            'Spotifice.MediaProviderAdapter.Endpoints': f'tcp -p {self.provider_port}',
            'Spotifice.MediaProvider.Content': str(MEDIA),
            'Spotifice.MediaProvider.Playlists': str(PLAYLISTS),
            'Spotifice.MediaProvider.Identity': self.provider_identity
        }
        provider_strprx = f'{self.provider_identity}:tcp -p {self.provider_port} -t 500'
        self.create_server(provider_main, provider_props)

        player = GstPlayer()
        player.start()
        self.addCleanup(player.shutdown)

        render_props = {
            'Spotifice.MediaRenderAdapter.Endpoints': f'tcp -p {self.render_port}',
            'Spotifice.MediaRender.Identity': self.render_identity
        }
        render_strprx = f'{self.render_identity}:tcp -p {self.render_port} -t 500'
        self.create_server(render_main, render_props, player)

        self.provider = self.create_proxy(provider_strprx, Spotifice.MediaProviderPrx)
        self.music_lib = self.stream_mngr = self.provider
        self.sut = self.create_proxy(render_strprx, Spotifice.MediaRenderPrx)

    def bind(self):
        self.sut.bind_media_provider(self.provider)


class PlaybackTestsBase(TestRender):
    def test_id(self):
        self.assertEqual(self.sut.ice_id(), '::Spotifice::MediaRender')

    def test_stop_is_idempotent(self):
        self.sut.stop()
        self.sut.stop()

    def test_play_unbound_provider(self):
        with self.assertRaises(Spotifice.BadReference) as cm:
            self.sut.play()

        self.assertEqual(cm.exception.reason, "No MediaProvider bound")

    def test_play_unloaded_track(self):
        self.bind()

        with self.assertRaises(Spotifice.TrackError) as cm:
            self.sut.play()

        self.assertEqual(cm.exception.reason, "No track loaded")

    def test_normal_play(self):
        self.bind()
        self.sut.load_track(identify(MEDIA / '2s.mp3'))

        self.sut.play()

    def test_can_not_play_if_player_busy(self):
        self.bind()
        self.sut.load_track(identify(MEDIA / '2s.mp3'))

        self.sut.play()

        with self.assertRaises(Spotifice.PlayerError) as cm:
            self.sut.play()

        self.assertEqual(cm.exception.reason, "Already playing")

    def test_load_track_while_playing(self):
        self.bind()
        self.sut.load_track(identify(MEDIA / '4s.mp3'))
        self.sut.play()

        self.sut.load_track(identify(MEDIA / '2s.mp3'))

        self.assertEqual(self.sut.get_current_track().id, identify(MEDIA / '2s.mp3'))


LONG = identify(MEDIA / '4s.mp3')


class PlayStopTestsBase(TestRender):
    def setUp(self):
        super().setUp()
        self.bind()
        self.sut.load_track(LONG)

    def test_play_when_playing_fails_with_the_expected_reason(self):
        self.sut.play()

        with self.assertRaises(Spotifice.PlayerError) as cm:
            self.sut.play()

        self.assertEqual(cm.exception.reason, "Already playing")

    def test_play_after_stop_plays_again(self):
        self.sut.play()
        self.sut.stop()
        self.sut.play()

        with self.assertRaises(Spotifice.PlayerError) as cm:
            self.sut.play()

        self.assertEqual(cm.exception.reason, "Already playing")

    def test_stop_keeps_the_current_track(self):
        self.sut.play()
        self.sut.stop()

        self.assertEqual(self.sut.get_current_track().id, LONG)


MEDIUM = identify(MEDIA / '2s.mp3')
SHORT = identify(MEDIA / '1s.mp3')


class RenderTestCase(TestRender):
    def current_id(self):
        track = self.sut.get_current_track()
        return track.id if track else None

    def is_playing(self):
        """play() solo falla con 'Already playing' si ya estaba sonando."""
        try:
            self.sut.play()
        except Spotifice.PlayerError as e:
            self.assertEqual(e.reason, "Already playing")
            return True

        self.sut.stop()
        return False


class LoadTrackTestsBase(RenderTestCase):
    def setUp(self):
        super().setUp()
        self.bind()
        self.sut.load_track(LONG)

    def test_load_track_when_stopped(self):
        self.sut.load_track(MEDIUM)

        self.assertEqual(self.current_id(), MEDIUM)
        self.assertFalse(self.is_playing())

    def test_load_track_when_playing_keeps_playing(self):
        self.sut.play()

        self.sut.load_track(MEDIUM)

        self.assertEqual(self.current_id(), MEDIUM)
        self.assertTrue(self.is_playing())

    def test_wrong_track_changes_nothing(self):
        self.sut.play()

        with self.assertRaises(Spotifice.TrackError):
            self.sut.load_track('bad-track-id')

        self.assertEqual(self.current_id(), LONG)
        self.assertTrue(self.is_playing())

    def test_reloading_the_current_track_when_playing_keeps_playing(self):
        self.sut.play()

        self.sut.load_track(LONG)

        self.assertEqual(self.current_id(), LONG)
        self.assertTrue(self.is_playing())

    def test_unbind_stops_playback(self):
        self.sut.play()

        self.sut.unbind_media_provider()

        self.bind()
        self.assertFalse(self.is_playing())


class PlaylistTestCase(RenderTestCase):
    def load_mix(self):
        self.bind()
        self.sut.load_playlist('mix')


class LoadPlaylistTestsBase(PlaylistTestCase):
    def test_load_playlist_loads_the_first_track(self):
        self.load_mix()
        self.assertEqual(self.current_id(), LONG)

    def test_load_playlist_does_not_start_playback(self):
        self.load_mix()
        self.assertFalse(self.is_playing())

    def test_load_playlist_while_playing_plays_the_first_track(self):
        self.bind()
        self.sut.load_track(SHORT)
        self.sut.play()

        self.sut.load_playlist('mix')

        self.assertEqual(self.current_id(), LONG)
        self.assertTrue(self.is_playing())

    def test_load_playlist_unbound_provider(self):
        with self.assertRaises(Spotifice.BadReference) as cm:
            self.sut.load_playlist('mix')

        self.assertEqual(cm.exception.reason, "No MediaProvider bound")

    def test_load_unknown_playlist(self):
        self.bind()
        self.sut.load_track(SHORT)

        with self.assertRaises(Spotifice.PlaylistError) as cm:
            self.sut.load_playlist('bad-playlist')

        self.assertEqual(cm.exception.item, 'bad-playlist')
        self.assertEqual(cm.exception.reason, "Playlist not found")
        self.assertEqual(self.current_id(), SHORT)

    def test_load_empty_playlist(self):
        self.bind()
        self.sut.load_track(SHORT)

        with self.assertRaises(Spotifice.PlaylistError):
            self.sut.load_playlist('empty')

        self.assertEqual(self.current_id(), SHORT)


class NavigationTestsBase(PlaylistTestCase):
    # next()
    def test_next(self):
        self.load_mix()
        self.sut.next()
        self.assertEqual(self.current_id(), MEDIUM)
        self.sut.next()
        self.assertEqual(self.current_id(), SHORT)

    def test_next_when_stopped_keeps_stopped(self):
        self.load_mix()
        self.sut.next()
        self.assertFalse(self.is_playing())

    def test_next_when_playing_keeps_playing(self):
        self.load_mix()
        self.sut.play()

        self.sut.next()

        self.assertEqual(self.current_id(), MEDIUM)
        self.assertTrue(self.is_playing())

    def test_next_on_the_last_track_does_nothing(self):
        self.load_mix()
        self.sut.next()
        self.sut.next()

        self.sut.next()

        self.assertEqual(self.current_id(), SHORT)

    def test_next_on_the_last_track_with_repeat_loads_the_first(self):
        self.load_mix()
        self.sut.set_repeat(True)
        self.sut.next()
        self.sut.next()

        self.sut.next()

        self.assertEqual(self.current_id(), LONG)

    # previous()
    def test_previous(self):
        self.load_mix()
        self.sut.next()
        self.sut.next()

        self.sut.previous()
        self.assertEqual(self.current_id(), MEDIUM)
        self.sut.previous()
        self.assertEqual(self.current_id(), LONG)

    def test_previous_on_the_first_track_does_nothing(self):
        self.load_mix()
        self.sut.previous()
        self.assertEqual(self.current_id(), LONG)

    def test_previous_on_the_first_track_does_nothing_even_with_repeat(self):
        self.load_mix()
        self.sut.set_repeat(True)
        self.sut.previous()
        self.assertEqual(self.current_id(), LONG)

    def test_previous_when_playing_keeps_playing(self):
        self.load_mix()
        self.sut.next()
        self.sut.play()

        self.sut.previous()

        self.assertEqual(self.current_id(), LONG)
        self.assertTrue(self.is_playing())

    def test_previous_when_stopped_keeps_stopped(self):
        self.load_mix()
        self.sut.next()
        self.sut.previous()
        self.assertFalse(self.is_playing())

    def test_playlist_loaded_after_discarding_unknown_tracks(self):
        self.bind()
        self.sut.load_playlist('partial')
        self.sut.next()
        self.assertEqual(self.current_id(), MEDIUM)


class WithoutPlaylistTestsBase(PlaylistTestCase):
    def test_next_and_previous_without_playlist_do_nothing(self):
        self.bind()
        self.sut.load_track(MEDIUM)

        self.sut.next()
        self.assertEqual(self.current_id(), MEDIUM)
        self.sut.previous()
        self.assertEqual(self.current_id(), MEDIUM)

    def test_next_and_previous_without_anything_loaded(self):
        self.sut.next()
        self.sut.previous()
        self.assertIsNone(self.sut.get_current_track())

    def test_set_repeat_without_playlist_only_stores_the_value(self):
        self.bind()
        self.sut.load_track(MEDIUM)

        self.sut.set_repeat(True)

        self.assertEqual(self.current_id(), MEDIUM)

    def test_load_track_discards_the_playlist(self):
        self.load_mix()
        self.sut.load_track(MEDIUM)

        self.sut.next()
        self.assertEqual(self.current_id(), MEDIUM)

    def test_bind_discards_the_playlist(self):
        self.load_mix()
        self.bind()

        self.sut.next()

        self.assertEqual(self.current_id(), LONG)

    def test_unbind_discards_the_playlist_and_stops(self):
        self.load_mix()
        self.sut.play()

        self.sut.unbind_media_provider()

        self.assertEqual(self.current_id(), LONG)
        self.bind()
        self.assertFalse(self.is_playing())
        self.sut.next()
        self.assertEqual(self.current_id(), LONG)

    def test_failed_load_playlist_keeps_the_previous_playlist(self):
        self.load_mix()

        with self.assertRaises(Spotifice.PlaylistError):
            self.sut.load_playlist('bad-playlist')

        self.sut.next()
        self.assertEqual(self.current_id(), MEDIUM)

    def test_example_sequence(self):
        self.bind()
        self.sut.load_playlist('mix')
        self.assertEqual((self.current_id(), self.is_playing()), (LONG, False))
        self.sut.play()
        self.assertEqual((self.current_id(), self.is_playing()), (LONG, True))
        self.sut.next()
        self.assertEqual((self.current_id(), self.is_playing()), (MEDIUM, True))
        self.sut.stop()
        self.sut.previous()
        self.assertEqual((self.current_id(), self.is_playing()), (LONG, False))
        self.sut.play()
        self.sut.set_repeat(enabled=True)
        self.sut.next()
        self.assertEqual((self.current_id(), self.is_playing()), (MEDIUM, True))