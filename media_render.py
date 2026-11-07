#!/usr/bin/env python3

import logging
import signal
import sys
import threading
import uuid
from contextlib import contextmanager
from time import sleep

import Ice
from Ice import identityToString as id2str

from gst_player import GstPlayer

Ice.loadSlice(f'-I{Ice.getSliceDir()} spotifice_v2.ice')
import Spotifice  # type: ignore # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MediaRender")


class MediaRenderI(Spotifice.MediaRender):
    def __init__(self, player):
        self.player = player
        self.provider = None
        self.current_track = None

        self.render_id = None

        self.playing = False
        self.repeat = False

        # Playlist cargada (None si no hay) y posicion de la pista actual en ella.
        self.playlist = None
        self.playlist_index = 0

        # Identifica el flujo en curso, para ignorar avisos atrasados de flujos anteriores.
        self.generation = 0

        # Los hilos de Ice y el hilo del player modifican este estado a la vez.
        self.lock = threading.RLock()


    def ensure_provider_bound(self):
        if not self.provider:
            raise Spotifice.BadReference(reason="No MediaProvider bound")

    def remember_identity(self, current):
        if current:
            self.render_id = current.id

    # --- Manejo del flujo (hay que tener el lock tomado) ---

    def start_stream(self, track):
        try:
            self.provider.open_stream(self.render_id, track.id)
        except Spotifice.BadIdentity as e:
            logger.error(f"Error starting stream: {e.reason}")
            raise Spotifice.StreamError(reason="Strean setup failed")

        self.generation += 1
        self.player.play(self.chunk_reader(self.render_id))

        if not self.player.confirm_play_starts():
            self.release_stream_quietly()
            raise Spotifice.PlayerError(reason="Failed to confirm playback")

        self.playing = True

    def halt_stream(self):
        self.generation += 1
        self.playing = False
        stopped = self.player.stop()
        self.close_provider_stream()
        if not stopped:
            raise Spotifice.PlayerError(reason="Failed to confirm stop")

    def release_stream_quietly(self):
        try:
            self.halt_stream()
        except Spotifice.PlayerError as e:
            logger.error(f"Could not release the stream: {e.reason}")

    def close_provider_stream(self):
        if self.provider and self.render_id:
            try:
                self.provider.close_stream(self.render_id)
            except Ice.Exception as e:
                logger.error(f"Could not close the stream: {e}")

    # --- RenderConnectivity ---

    def bind_media_provider(self, media_provider, current=None):
        try:
            proxy = media_provider.ice_timeout(500)
            proxy.ice_ping()
        except Ice.ConnectionRefusedException as e:
            raise Spotifice.BadReference(reason=f"MediaProvider not reachable: {e}")

        self.provider = media_provider
        logger.info(f"Bound to MediaProvider '{id2str(media_provider.ice_getIdentity())}'")

    def unbind_media_provider(self, current=None):
        try:
            self.stop(current)
        except Spotifice.PlayerError as e:
            logger.error(f"Unbinding despite stop failure: {e.reason}")

        self.provider = None
        logger.info("Unbound MediaProvider")

    # --- ContentManager ---

    def load_track(self, track_id, current=None):
        self.ensure_provider_bound()

        try:
            with self.keep_playing_state(current):
                self.current_track = self.provider.get_track_info(track_id)

            logger.info(f"Current track set to: {self.current_track.title}")

        except Spotifice.TrackError as e:
            logger.error(f"Error setting track: {e.reason}")
            raise

    def get_current_track(self, current=None):
        return self.current_track

    # --- PlaybackController ---

    @contextmanager
    def keep_playing_state(self, current):
        playing = self.player.is_playing()
        if playing:
            self.stop(current)
        try:
            yield
        finally:
            if playing:
                self.play(current)

    def play(self, current=None):
        assert current, "remote invocation required"

        with self.lock:
            self.remember_identity(current)
            self.ensure_provider_bound()

            if not self.current_track:
                raise Spotifice.TrackError(reason="No track loaded")

            if self.playing:
                raise Spotifice.PlayerError(reason="Already playing")

            self.start_stream(self.current_track)

    def chunk_reader(self, render_id):
        provider = self.provider

        def read_chunk(chunk_size):
            try:
                return provider.get_chunk(render_id, chunk_size)
            except Spotifice.IOError as e:
                logger.error(f"Playback of track '{e.item}' aborted: {e.reason}")
            except Ice.Exception as e:
                logger.critical(e)

        return read_chunk

    def stop(self, current=None):
        with self.lock:
            self.remember_identity(current)
            self.halt_stream()

        logger.info("Stopped")



def shutdown_on_interrupt(ic):
    def shutdown(*_):
        ic.shutdown()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT,  shutdown)
        signal.signal(signal.SIGTERM, shutdown)


def main(ic, player):
    shutdown_on_interrupt(ic)

    servant = MediaRenderI(player)

    properties = ic.getProperties()
    identity = properties.getPropertyWithDefault(
        'Spotifice.MediaRender.Identity',  str(uuid.uuid1()))

    adapter = ic.createObjectAdapter('Spotifice.MediaRenderAdapter')
    proxy = adapter.add(servant, ic.stringToIdentity(identity))
    logger.info(f"MediaRender: {proxy}")

    adapter.activate()
    while not ic.isShutdown():
        sleep(0.5)

    logger.info("Server shutdown.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: media_render.py --Ice.Config=<config-file>")

    with Ice.initialize(sys.argv) as communicator, GstPlayer() as player:
        main(communicator, player)
