#!/usr/bin/env python3
"""
Galaxian (Namco, 1979) - Python port.

    python galaxian.py [--scale N] [--lives 2|3]
                       [--bonus 7000|10000|12000|20000] [--coinage 1C1C|2C1C|1C2C|FREE]
                       [--fullscreen] [--mute] [--death-sample WAV] [--bezel PNG]

Needs pygame and numpy (pip install pygame numpy).  Everything else,
graphics and colours included, is built in - no ROM files are needed.

Keys
    5            insert coin
    1 / 2        one / two player start
    Left/Right   move (also A / D)
    Space / Ctrl fire
    F3           reset (power cycle)
    F5           pause / unpause
    F12          save screenshot (screenshots/ folder)
    B            show / hide the cabinet bezel (if one is loaded)
    F11          fullscreen on / off (also Alt+Enter)
    Esc          quit
"""

import argparse
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from game import Galaxian                      # noqa: E402
from video import Video                        # noqa: E402
from gfxdata import ROMS                       # noqa: E402
from sound import Synth, RATE, find_death_sample  # noqa: E402

FPS = 6144000 / 384 / 264      # 60.606 Hz, the board's refresh rate
W, H = 224, 256


def parse_args():
    p = argparse.ArgumentParser(description='Galaxian (Namco 1979) in Python')
    p.add_argument('--scale', type=int, default=3)
    p.add_argument('--lives', type=int, choices=(2, 3), default=3)
    p.add_argument('--bonus', type=int, choices=(7000, 10000, 12000, 20000), default=7000)
    p.add_argument('--coinage', choices=('1C1C', '2C1C', '1C2C', 'FREE'), default='1C1C')
    p.add_argument('--fullscreen', action='store_true')
    p.add_argument('--mute', action='store_true')
    p.add_argument('--death-sample', help='optional WAV to use for the player explosion')
    p.add_argument('--bezel', help='optional cabinet artwork PNG with a transparent screen opening '
                                   '(also picked up automatically as bezel.png next to the game)')
    return p.parse_args()


def find_bezel(explicit):
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    for d in (HERE, os.getcwd()):
        for name in ('bezel.png', os.path.join('bezels', 'galaxian.png')):
            fp = os.path.join(d, name)
            if os.path.isfile(fp):
                return fp
    return None


def screen_opening(pygame, surf):
    """Bounding box of the transparent screen hole in a bezel image."""
    w, h = surf.get_size()
    try:
        alpha = pygame.surfarray.array_alpha(surf)            # [x][y]
        hole = alpha < 128
        xs = np.where(hole.any(axis=1))[0]
        ys = np.where(hole.any(axis=0))[0]
        if len(xs) and len(ys) and hole.sum() > 0.05 * w * h:
            return pygame.Rect(int(xs[0]), int(ys[0]), int(xs[-1] - xs[0] + 1), int(ys[-1] - ys[0] + 1))
    except Exception:
        pass
    # no transparency: assume a centred 3:4 screen filling the height
    sw = int(h * 3 / 4)
    return pygame.Rect((w - sw) // 2, 0, sw, h)


def main():
    args = parse_args()
    import pygame

    video = Video(ROMS)

    dips = dict(coinage={'1C1C': 0, '2C1C': 1, '1C2C': 2, 'FREE': 3}[args.coinage],
                bonus={7000: 0, 10000: 1, 12000: 2, 20000: 3}[args.bonus],
                lives=args.lives)
    game = Galaxian(dips)

    audio = None
    if not args.mute:
        try:
            pygame.mixer.pre_init(RATE, -16, 1, 512)
        except Exception:
            pass
    pygame.init()
    if not args.mute:
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(RATE, -16, 1, 512)
            freq, _, chans = pygame.mixer.get_init()
            death = find_death_sample([HERE, os.getcwd()], args.death_sample)
            if death:
                print('using death.wav sample for the player explosion')
            audio = (pygame.mixer.Channel(0), Synth(freq, death_sample=death), chans)
        except Exception as e:
            print('audio disabled:', e)
            audio = None

    pygame.display.set_caption('Galaxian')
    info = pygame.display.Info()
    desk_w, desk_h = info.current_w, info.current_h
    try:                                    # pygame 2: true desktop size, unaffected by our window
        desk_w, desk_h = pygame.display.get_desktop_sizes()[0]
    except Exception:
        pass

    bezel_img = None
    bezel_path = find_bezel(args.bezel)
    if args.bezel and not bezel_path:
        print('bezel file not found:', args.bezel)
    if bezel_path:
        try:
            bezel_img = pygame.image.load(bezel_path)
            print('bezel:', bezel_path, '(press B to toggle)')
        except Exception as e:
            print('could not load bezel:', e)
    view = {'bezel': bezel_img is not None, 'full': args.fullscreen}

    def setup_display():
        """(Re)create the window for the current view; returns the layout."""
        if view['bezel']:
            bw, bh = bezel_img.get_size()
            if view['full']:
                win = (desk_w, desk_h)
                screen = pygame.display.set_mode(win, pygame.FULLSCREEN)
            else:
                f = min(1.0, 0.92 * desk_w / bw, 0.90 * desk_h / bh) if desk_w and desk_h else 1.0
                win = (int(bw * f), int(bh * f))
                screen = pygame.display.set_mode(win)
            f = min(win[0] / bw, win[1] / bh)
            size = (int(bw * f), int(bh * f))
            off = ((win[0] - size[0]) // 2, (win[1] - size[1]) // 2)
            overlay = bezel_img.convert_alpha()
            if size != (bw, bh):                # 1:1 on a matching display, else scaled to fit
                overlay = pygame.transform.smoothscale(overlay, size)
            hole = screen_opening(pygame, bezel_img)
            rect = pygame.Rect(off[0] + int(hole.x * f), off[1] + int(hole.y * f),
                               int(hole.w * f) + 1, int(hole.h * f) + 1)
            return screen, rect, overlay, off
        if view['full']:
            screen = pygame.display.set_mode((desk_w, desk_h), pygame.FULLSCREEN)
            sc = max(1, min(desk_w // W, desk_h // H))
        else:
            sc = max(1, args.scale)
            screen = pygame.display.set_mode((W * sc, H * sc))
        sw, sh = screen.get_size()
        rect = pygame.Rect((sw - W * sc) // 2, (sh - H * sc) // 2, W * sc, H * sc)
        return screen, rect, None, (0, 0)

    screen, game_rect, overlay, overlay_pos = setup_display()
    frame_surf = pygame.Surface((W, H))
    font = pygame.font.Font(None, 28)

    paused = False
    coin_pulse = 0
    chunk = 1024
    period = 1.0 / FPS
    next_t = time.perf_counter()
    last_frame = None

    def keys_to_inputs():
        k = pygame.key.get_pressed()
        in0 = 0
        if k[pygame.K_LEFT] or k[pygame.K_a]:
            in0 |= 0x04
        if k[pygame.K_RIGHT] or k[pygame.K_d]:
            in0 |= 0x08
        if k[pygame.K_SPACE] or k[pygame.K_LCTRL] or k[pygame.K_RCTRL]:
            in0 |= 0x10
        in1 = 0
        if k[pygame.K_1]:
            in1 |= 0x01
        if k[pygame.K_2]:
            in1 |= 0x02
        # an upright cabinet's player 2 uses the same controls
        in1 |= (in0 & 0x1C)
        return in0, in1

    def save_screenshot():
        d = os.path.join(HERE, 'screenshots')
        os.makedirs(d, exist_ok=True)
        name = os.path.join(d, time.strftime('galaxian_%Y%m%d_%H%M%S.png'))
        if last_frame is not None:
            pygame.image.save(pygame.transform.scale(last_frame, (W * 3, H * 3)), name)
            print('saved', name)

    running = True
    redraw_now = False
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_5:
                    coin_pulse = 4
                elif ev.key == pygame.K_F3:
                    game.power_on()
                    if audio:
                        audio[0].stop()
                elif ev.key == pygame.K_F5:
                    paused = not paused
                    if audio:
                        audio[0].pause() if paused else audio[0].unpause()
                elif ev.key == pygame.K_F12:
                    save_screenshot()
                elif ev.key == pygame.K_F11 or (ev.key == pygame.K_RETURN and ev.mod & pygame.KMOD_ALT):
                    view['full'] = not view['full']
                    screen, game_rect, overlay, overlay_pos = setup_display()
                    redraw_now = True
                elif ev.key == pygame.K_b and bezel_img is not None:
                    view['bezel'] = not view['bezel']
                    screen, game_rect, overlay, overlay_pos = setup_display()
                    redraw_now = True

        now = time.perf_counter()
        redraw = paused or redraw_now
        redraw_now = False
        if not paused and now >= next_t:
            redraw = True
            in0, in1 = keys_to_inputs()
            if coin_pulse:
                in0 |= 0x01
                coin_pulse -= 1
            game.in0 = in0
            game.in1 = in1
            game.frame()
            if audio:
                ch, synth, chans = audio
                if ch.get_queue() is None:
                    s = (synth.generate(game.m, chunk) * 30000).astype(np.int16)
                    if chans == 2:
                        s = np.repeat(s[:, None], 2, axis=1)
                    snd = pygame.sndarray.make_sound(np.ascontiguousarray(s))
                    if ch.get_busy():
                        ch.queue(snd)
                    else:
                        ch.play(snd)
            pygame.surfarray.blit_array(frame_surf, video.render(game.m))
            last_frame = frame_surf
            next_t += period
            if now - next_t > 0.25:          # fell far behind: resync
                next_t = now
        elif paused:
            next_t = now

        if redraw and last_frame is not None:
            if overlay is not None:
                screen.fill((0, 0, 0))
            screen.blit(pygame.transform.scale(last_frame, game_rect.size), game_rect.topleft)
            if overlay is not None:
                screen.blit(overlay, overlay_pos)
            if paused:
                txt = font.render('PAUSED  (F5)', True, (255, 255, 0))
                screen.blit(txt, (game_rect.centerx - txt.get_width() // 2,
                                  game_rect.centery + game_rect.h // 6))
            pygame.display.flip()
        time.sleep(max(0.0, min(next_t - time.perf_counter(), 0.004)))

    pygame.quit()


if __name__ == '__main__':
    main()
