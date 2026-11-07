#!/usr/bin/env python3

import sys
from time import sleep

import Ice

Ice.loadSlice(f'-I{Ice.getSliceDir()} spotifice_v2.ice')
import Spotifice  # type: ignore # noqa: E402


def get_proxy(ic, property, cls):
    proxy = ic.propertyToProxy(property)

    for _ in range(5):
        try:
            proxy.ice_ping()
            break
        except Ice.ConnectionRefusedException:
            sleep(0.5)

    object = cls.checkedCast(proxy)
    if object is None:
        raise RuntimeError(f'Invalid proxy for {property}')

    return object

def show_current(render, operation):
    track = render.get_current_track()
    print(f"{operation:>14}: {track.title if track else None}")


def main(ic):
    provider = get_proxy(ic, 'Spotifice.MediaProvider.Proxy', Spotifice.MediaProviderPrx)
    render = get_proxy(ic, 'Spotifice.MediaRender.Proxy', Spotifice.MediaRenderPrx)

    print("Fetching all tracks...")
    tracks = provider.get_all_tracks()
    for t in tracks:
        print(f"- {t.title}")

    if not tracks:
        print("No tracks found.")
        return

    print(f"Requesting info for track {tracks[0].id}")
    print(f"Track title: {tracks[0].title}")

    print("Fetching all playlists...")
    playlists = provider.get_all_playlists()
    for p in playlists:
        print(f"- {p.name} ({p.id}): {len(p.track_ids)} tracks")

    # La demo usa la playlist con mas pistas
    playlist = max(playlists, key=lambda p: len(p.track_ids), default=None)
    if not playlist or len(playlist.track_ids) < 2:
        print("Hace falta una playlist con al menos 2 pistas para la demo.")
        return

    render.bind_media_provider(provider)
    render.stop()

    render.load_playlist(playlist.id)
    show_current(render, "load_playlist")

    render.play()
    show_current(render, "play")
    sleep(3)  # Let it play for 3 seconds

    render.next()
    show_current(render, "next")

    render.stop()
    show_current(render, "stop")

    render.previous()
    show_current(render, "previous")

    render.play()
    show_current(render, "play")

    render.set_repeat(enabled=True)
    show_current(render, "set_repeat")

    render.next()
    show_current(render, "next")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit("Usage: media_control.py --Ice.Config=<config-file>")

    with Ice.initialize(sys.argv) as communicator:
        main(communicator)
