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

Ice.loadSlice(f'-I{Ice.getSliceDir()} spotifice_v1.ice')
import Spotifice  # type: ignore # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MediaRender")
PLAYING = Spotifice.PlaybackState.PLAYING
PAUSED = Spotifice.PlaybackState.PAUSED
STOPPED = Spotifice.PlaybackState.STOPPED


class MediaRenderI(Spotifice.MediaRender):
    def __init__(self, player):
        self.player = player
        self.provider = None
        self.current_track = None
        self.render_id = None

        self.state = STOPPED
        self.repeat = False

        # Ids de las pistas cargadas. La última es la pista actual.
        self.history = []

        # True mientras el player tiene un flujo abierto (sonando o en pausa).
        self.stream_open = False

        # Identifica el flujo en curso. Sirve para ignorar avisos de fin de
        # pista que lleguen tarde, de un flujo anterior.
        self.generation = 0

        # Los hilos de Ice y el hilo del player modifican el estado a la vez.
        self.lock = threading.RLock()

    def ensure_player_stopped(self):
        if self.player.is_playing():
            raise Spotifice.PlayerError(reason="Already playing")

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
        generation = self.generation
        self.player.play(
            self.chunk_reader(self.render_id),
            lambda: self.on_track_exhausted(generation))

        if not self.player.confirm_play_starts():
            self.release_stream_quietly()
            raise Spotifice.PlayerError(reason="Failed to confirm playback")

        self.stream_open = True

    def halt_stream(self):
        self.generation += 1
        self.stream_open = False
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

    def switch_to(self, track):
        """Pone `track` como pista actual conservando el estado: si sonaba,
        suena la nueva desde el principio; si estaba en pausa o parado, espera
        a play()."""
        was_playing = self.state == PLAYING

        if self.state != STOPPED:
            self.halt_stream()

        if was_playing:
            try:
                self.start_stream(track)
            except Exception:
                self.state = STOPPED
                raise

        self.current_track = track

    def on_track_exhausted(self, generation):
        with self.lock:
            if generation != self.generation or self.state == STOPPED:
                return

            # El player ya ha desmontado el pipeline.
            self.stream_open = False

            if self.state == PAUSED:
                return

            if self.repeat:
                try:
                    self.start_stream(self.current_track)
                    logger.info("Track repeated")
                    return
                except (Spotifice.Error, Ice.Exception) as e:
                    logger.error(f"Could not repeat the track: {e}")

            self.state = STOPPED
            logger.info("Track finished")

    # --- RenderConnectivity ---

    # sustituir bind_media_provider. Solo cambia el final del método
    def bind_media_provider(self, media_provider, current=None):
        try:
            proxy = media_provider.ice_timeout(500)
            proxy.ice_ping()
        except Ice.ConnectionRefusedException as e:
            raise Spotifice.BadReference(reason=f"MediaProvider not reachable: {e}")

        with self.lock:
            self.remember_identity(current)
            self.provider = media_provider
            self.history.clear()

        logger.info(f"Bound to MediaProvider '{id2str(media_provider.ice_getIdentity())}'")

    # sustituir unbind_media_provider
    def unbind_media_provider(self, current=None):
        with self.lock:
            try:
                self.stop(current)
            except Spotifice.PlayerError as e:
                logger.error(f"Unbinding despite stop failure: {e.reason}")

            self.provider = None
            self.history.clear()

        logger.info("Unbound MediaProvider")

    # --- ContentManager ---

    def load_track(self, track_id, current=None):
        with self.lock:
            self.remember_identity(current)
            self.ensure_provider_bound()

            try:
                track = self.provider.get_track_info(track_id)
            except Spotifice.TrackError as e:
                logger.error(f"Error setting track: {e.reason}")
                raise

            self.switch_to(track)

            if not self.history or self.history[-1] != track.id:
                self.history.append(track.id)

            logger.info(f"Current track set to: {track.title}")

    def get_current_track(self, current=None):
        return self.current_track

    # --- PlaybackController ---


    def play(self, current=None):
        assert current, "remote invocation required"

        with self.lock:
            self.remember_identity(current)
            self.ensure_provider_bound()

            if not self.current_track:
                raise Spotifice.TrackError(reason="No track loaded")

            if self.state == PLAYING:
                raise Spotifice.PlayerError(reason="Already playing")


            if self.state == PAUSED and self.stream_open:
                if not self.player.resume():
                    raise Spotifice.PlayerError(reason="Failed to resume playback")
            else:
                self.start_stream(self.current_track)

            self.state = PLAYING


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
            try:
                self.halt_stream()
            finally:
                self.state = STOPPED

        logger.info("Stopped")


    def pause(self, current=None):
        with self.lock:
            if self.state == STOPPED:
                raise Spotifice.PlayerError(reason="Cannot pause when stopped")

            if self.state == PAUSED:
                return

            if not self.player.pause():
                raise Spotifice.PlayerError(reason="Failed to confirm pause")

            self.state = PAUSED

        logger.info("Paused")

    def previous(self, current=None):
        with self.lock:
            self.remember_identity(current)

            if len(self.history) < 2:
                return

            self.ensure_provider_bound()
            track = self.provider.get_track_info(self.history[-2])
            self.switch_to(track)
            self.history.pop()

            logger.info(f"Back to track: {track.title}")

    # get_status() devuelve un objeto PlaybackStatus con los tres campos que define spotifice_v1.ice. 
    # Si no hay pista cargada, current_track vale None, que es lo que pide el enunciado (“nula si no hay ninguna cargada”).
    def get_status(self, current=None):
        with self.lock:
            return Spotifice.PlaybackStatus(
                state=self.state,
                current_track=self.current_track,
                is_repeating=self.repeat)

    # set_repeat() solo guarda el valor. El enunciado dice que está desactivada 
    # por defecto, y eso ya lo hicimos con self.repeat = False.
    def set_repeat(self, enabled, current=None):
        # with self.lock: protege la lectura y la escritura, porque más adelante el hilo del player también modificará estos datos.
        with self.lock:
            self.repeat = enabled

        logger.info(f"Repeat {'enabled' if enabled else 'disabled'}")


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
