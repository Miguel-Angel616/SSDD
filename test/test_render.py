from pathlib import Path

from media_provider import main as provider_main
from media_render import Spotifice
from media_render import main as render_main
from metadata import identify

from gst_player import GstPlayer

from .icetest import IceTestCase
from time import monotonic, sleep

MEDIA = Path('test/media')


class TestRender(IceTestCase):
    provider_port = 10000
    provider_identity = 'mediaProvider0'
    render_port = 10001
    render_identity = 'mediaRender0'

    def setUp(self):
        provider_props = {
            'Spotifice.MediaProviderAdapter.Endpoints': f'tcp -p {self.provider_port}',
            'Spotifice.MediaProvider.Content': str(MEDIA),
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


PLAYING = Spotifice.PlaybackState.PLAYING
PAUSED = Spotifice.PlaybackState.PAUSED
STOPPED = Spotifice.PlaybackState.STOPPED


class StatusTestsBase(TestRender):
    def test_initial_status(self):
        status = self.sut.get_status()

        self.assertEqual(status.state, STOPPED)
        self.assertIsNone(status.current_track)
        self.assertFalse(status.is_repeating)

    def test_set_repeat(self):
        self.sut.set_repeat(True)
        self.assertTrue(self.sut.get_status().is_repeating)

        self.sut.set_repeat(False)
        self.assertFalse(self.sut.get_status().is_repeating)

    def test_status_includes_the_current_track(self):
        self.bind()
        track_id = identify(MEDIA / '2s.mp3')
        self.sut.load_track(track_id)

        self.assertEqual(self.sut.get_status().current_track.id, track_id)


LONG = identify(MEDIA / '4s.mp3')


class PlayStopStateTestsBase(TestRender):
    def setUp(self):
        super().setUp()
        self.bind()
        self.sut.load_track(LONG)

    def state(self):
        return self.sut.get_status().state

    def test_play_changes_the_state_to_playing(self):
        self.sut.play()
        self.assertEqual(self.state(), PLAYING)

    def test_stop_changes_the_state_to_stopped(self):
        self.sut.play()
        self.sut.stop()
        self.assertEqual(self.state(), STOPPED)

    def test_stop_keeps_the_current_track(self):
        self.sut.play()
        self.sut.stop()
        self.assertEqual(self.sut.get_status().current_track.id, LONG)

    def test_play_after_stop(self):
        self.sut.play()
        self.sut.stop()
        self.sut.play()
        self.assertEqual(self.state(), PLAYING)

    def test_play_when_playing_fails_and_keeps_playing(self):
        self.sut.play()

        with self.assertRaises(Spotifice.PlayerError) as cm:
            self.sut.play()

        self.assertEqual(cm.exception.reason, "Already playing")
        self.assertEqual(self.state(), PLAYING)

    def test_unbind_stops_playback(self):
        self.sut.play()
        self.sut.unbind_media_provider()
        self.assertEqual(self.state(), STOPPED)


class PauseTestsBase(TestRender):
    def setUp(self):
        super().setUp()
        self.bind()
        self.sut.load_track(LONG)

    def state(self):
        return self.sut.get_status().state

    def test_pause_when_playing(self):
        self.sut.play()
        self.sut.pause()
        self.assertEqual(self.state(), PAUSED)

    def test_pause_when_paused_is_idempotent(self):
        self.sut.play()
        self.sut.pause()
        self.sut.pause()
        self.assertEqual(self.state(), PAUSED)

    def test_pause_when_stopped(self):
        with self.assertRaises(Spotifice.PlayerError) as cm:
            self.sut.pause()

        self.assertEqual(cm.exception.reason, "Cannot pause when stopped")
        self.assertEqual(self.state(), STOPPED)

    def test_play_when_paused_resumes(self):
        self.sut.play()
        self.sut.pause()
        self.sut.play()
        self.assertEqual(self.state(), PLAYING)

    def test_stop_when_paused(self):
        self.sut.play()
        self.sut.pause()
        self.sut.stop()
        self.assertEqual(self.state(), STOPPED)

    def test_pause_keeps_the_current_track(self):
        self.sut.play()
        self.sut.pause()
        self.assertEqual(self.sut.get_status().current_track.id, LONG)

    def test_play_after_stop_when_paused_starts_again(self):
        self.sut.play()
        self.sut.pause()
        self.sut.stop()
        self.sut.play()
        self.assertEqual(self.state(), PLAYING)



MEDIUM = identify(MEDIA / '2s.mp3')


class LoadTrackStateTestsBase(TestRender):
    def setUp(self):
        super().setUp()
        self.bind()
        self.sut.load_track(LONG)

    def assert_status(self, state, track_id):
        status = self.sut.get_status()
        self.assertEqual(status.state, state)
        self.assertEqual(status.current_track.id, track_id)

    def test_load_track_when_stopped(self):
        self.sut.load_track(MEDIUM)
        self.assert_status(STOPPED, MEDIUM)

    def test_load_track_when_playing(self):
        self.sut.play()
        self.sut.load_track(MEDIUM)
        self.assert_status(PLAYING, MEDIUM)

    def test_load_track_when_paused(self):
        self.sut.play()
        self.sut.pause()
        self.sut.load_track(MEDIUM)
        self.assert_status(PAUSED, MEDIUM)

    def test_new_track_loaded_when_paused_plays_on_play(self):
        self.sut.play()
        self.sut.pause()
        self.sut.load_track(MEDIUM)

        self.sut.play()

        self.assert_status(PLAYING, MEDIUM)

    def test_wrong_track_does_not_change_anything(self):
        self.sut.play()

        with self.assertRaises(Spotifice.TrackError):
            self.sut.load_track('bad-track-id')

        self.assert_status(PLAYING, LONG)

    def test_stop_after_changing_track_when_paused(self):
        self.sut.play()
        self.sut.pause()
        self.sut.load_track(MEDIUM)
        self.sut.stop()
        self.assert_status(STOPPED, MEDIUM)


SHORT = identify(MEDIA / '1s.mp3')


class HistoryTestsBase(TestRender):
    A, B, C = LONG, MEDIUM, SHORT

    def run_sequence(self, *operations):
        self.bind()
        for operation in operations:
            if operation == 'previous':
                self.sut.previous()
            else:
                self.sut.load_track(operation)

    def current_id(self):
        track = self.sut.get_status().current_track
        return track.id if track else None

    # Los cinco ejemplos del Cuadro 3
    def test_previous(self):
        self.run_sequence(self.A, self.B, 'previous')
        self.assertEqual(self.current_id(), self.A)

    def test_previous_twice_goes_further_back(self):
        self.run_sequence(self.A, self.B, self.C, 'previous', 'previous')
        self.assertEqual(self.current_id(), self.A)

    def test_previous_without_previous_track_does_nothing(self):
        self.run_sequence(self.A, self.B, 'previous', 'previous')
        self.assertEqual(self.current_id(), self.A)

    def test_reloading_the_current_track_does_not_change_the_history(self):
        self.run_sequence(self.A, self.B, self.B, 'previous')
        self.assertEqual(self.current_id(), self.A)

    def test_abandoned_track_is_discarded(self):
        self.run_sequence(self.A, self.B, 'previous', self.C, 'previous')
        self.assertEqual(self.current_id(), self.A)

    # Otros casos
    def test_previous_does_not_return_to_the_abandoned_track(self):
        self.run_sequence(self.A, self.B, 'previous', 'previous', 'previous')
        self.assertEqual(self.current_id(), self.A)

    def test_the_same_track_can_be_in_the_history_twice(self):
        self.run_sequence(self.A, self.B, self.A, 'previous')
        self.assertEqual(self.current_id(), self.B)

    def test_previous_without_history(self):
        self.bind()
        self.sut.previous()
        self.assertIsNone(self.current_id())

    def test_bind_clears_the_history(self):
        self.run_sequence(self.A, self.B)
        self.bind()

        self.sut.previous()

        self.assertEqual(self.current_id(), self.B)

    def test_unbind_clears_the_history(self):
        self.run_sequence(self.A, self.B)
        self.sut.unbind_media_provider()
        self.bind()

        self.sut.previous()

        self.assertEqual(self.current_id(), self.B)


class PreviousStateTestsBase(TestRender):
    def setUp(self):
        super().setUp()
        self.bind()
        self.sut.load_track(MEDIUM)
        self.sut.load_track(LONG)

    def assert_status(self, state, track_id):
        status = self.sut.get_status()
        self.assertEqual(status.state, state)
        self.assertEqual(status.current_track.id, track_id)

    def test_previous_when_stopped(self):
        self.sut.previous()
        self.assert_status(STOPPED, MEDIUM)

    def test_previous_when_playing(self):
        self.sut.play()
        self.sut.previous()
        self.assert_status(PLAYING, MEDIUM)

    def test_previous_when_paused(self):
        self.sut.play()
        self.sut.pause()
        self.sut.previous()
        self.assert_status(PAUSED, MEDIUM)



class EndOfTrackTestsBase(TestRender):
    def setUp(self):
        super().setUp()
        self.bind()
        self.sut.load_track(SHORT)

    def status(self):
        return self.sut.get_status()

    def wait_for_state(self, state, timeout_secs=6):
        deadline = monotonic() + timeout_secs
        while monotonic() < deadline:
            if self.status().state == state:
                return True
            sleep(0.1)
        return False

    def test_track_ends_without_repeat(self):
        self.sut.play()

        self.assertTrue(self.wait_for_state(STOPPED))
        self.assertEqual(self.status().current_track.id, SHORT)

    def test_track_ends_with_repeat(self):
        self.sut.play()
        self.sut.set_repeat(True)

        sleep(2.5)  # más del doble de lo que dura la pista

        self.assertEqual(self.status().state, PLAYING)

        self.sut.set_repeat(False)
        self.assertTrue(self.wait_for_state(STOPPED))
        self.assertEqual(self.status().current_track.id, SHORT)

    def test_can_play_again_after_the_track_ends(self):
        self.sut.play()
        self.assertTrue(self.wait_for_state(STOPPED))

        self.sut.play()

        self.assertEqual(self.status().state, PLAYING)

    def test_history_does_not_change_when_a_track_ends(self):
        self.sut.load_track(MEDIUM)
        self.sut.load_track(SHORT)
        self.sut.play()
        self.assertTrue(self.wait_for_state(STOPPED))

        self.sut.previous()

        self.assertEqual(self.status().current_track.id, MEDIUM)