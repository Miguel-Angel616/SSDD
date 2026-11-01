#!/usr/bin/env python3

import sys
from time import sleep

import Ice

Ice.loadSlice(f'-I{Ice.getSliceDir()} spotifice_v1.ice')
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


def show_status(render, operation):
    status = render.get_status()
    title = status.current_track.title if status.current_track else None
    print(f"{operation:>12}: {status.state}, {title}, repeat={status.is_repeating}")


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

    if len(tracks) < 3:
        print("Hacen falta al menos 3 pistas para la demo.")
        return

    # Las pistas de prueba duran 1, 2 y 4 s: usamos las más largas para que
    # no terminen a mitad de la demo.
    track1, track2 = tracks[2], tracks[1]

    render.bind_media_provider(provider)
    render.stop()
    show_status(render, "start")

    render.load_track(track1.id)
    render.play()
    show_status(render, "play")

    render.load_track(track2.id)
    show_status(render, "load_track")

    render.pause()
    show_status(render, "pause")

    render.previous()
    show_status(render, "previous")

    render.play()
    show_status(render, "play")

    render.set_repeat(enabled=True)
    show_status(render, "set_repeat")

    render.stop()
    show_status(render, "stop")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit("Usage: media_control.py --Ice.Config=<config-file>")

    with Ice.initialize(sys.argv) as communicator:
        main(communicator)
