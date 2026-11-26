"""
Galaxian video: background tilemap with per-column scroll and colour,
8 sprites, 8 bullets (7 shells + 1 missile) and the LFSR starfield.

Graphics and colours come from gfxdata.py (the original 1h/1k/6l data).
The picture is built in the board's native (landscape) orientation, 256x224,
then turned 90 degrees into the upright 224x256 the player sees.
"""

import numpy as np

def _palette(prom):
    def w3(b0, b1, b2):
        return 0x21 * b0 + 0x47 * b1 + 0x97 * b2

    pal = np.zeros((32, 3), np.uint8)
    for i in range(32):
        v = prom[i]
        bit = lambda n: (v >> n) & 1
        pal[i] = (w3(bit(0), bit(1), bit(2)),
                  w3(bit(3), bit(4), bit(5)),
                  0x51 * bit(6) + 0xAE * bit(7))
    return pal


def _decode(gfx):
    half = len(gfx) // 2
    p0 = np.frombuffer(gfx[:half], np.uint8)     # 1h = high bit of the pixel
    p1 = np.frombuffer(gfx[half:], np.uint8)     # 1k = low bit
    bits = np.arange(7, -1, -1)
    hi = (p0[:, None] >> bits) & 1
    lo = (p1[:, None] >> bits) & 1
    pix = (hi << 1 | lo).astype(np.uint8)        # (2048 bytes, 8 pixels)
    chars = pix.reshape(256, 8, 8)               # [code][y][x]
    s = pix.reshape(64, 4, 8, 8)                 # quadrant order: TL, TR, BL, BR
    sprites = np.zeros((64, 16, 16), np.uint8)
    sprites[:, 0:8, 0:8] = s[:, 0]
    sprites[:, 0:8, 8:16] = s[:, 1]
    sprites[:, 8:16, 0:8] = s[:, 2]
    sprites[:, 8:16, 8:16] = s[:, 3]
    return chars, sprites


STAR_RNG_PERIOD = (1 << 17) - 1


def _stars():
    stars = np.zeros(STAR_RNG_PERIOD, np.uint8)
    sr = 0
    for i in range(STAR_RNG_PERIOD):
        enabled = (sr & 0x1FE01) == 0x1FE00
        stars[i] = ((~sr & 0x1F8) >> 3) | (0x80 if enabled else 0)
        sr = (sr >> 1) | ((((sr >> 12) ^ ~sr) & 1) << 16)
    lv = np.array([0x00, 0xC2, 0xD6, 0xFF], np.uint8)
    cols = np.zeros((64, 3), np.uint8)
    for c in range(64):
        cols[c] = (lv[c & 3], lv[(c >> 2) & 3], lv[(c >> 4) & 3])
    return stars, cols


class Video:
    def __init__(self, roms=None):
        if roms is None:
            from gfxdata import ROMS as roms
        gfx = roms['1h.bin'] + roms['1k.bin']
        self.chars, self.sprites = _decode(gfx)
        self.pal = _palette(roms['6l.bpr'])
        self.stars, self.star_cols = _stars()
        self.star_origin = 0
        yy = np.arange(256)[:, None]
        xx = np.arange(256)[None, :]
        self._yy = yy
        self._xx = xx
        self._star_base = (yy * 512 + xx * 2 + 1)            # 2nd RNG clock of each pixel
        self._star_enable = ((yy ^ (xx >> 3)) & 1).astype(bool)
        self._bullet_cols = np.array([[0xEF, 0xEF, 0xEF]] * 7 + [[0xEF, 0xEF, 0x00]], np.uint8)

    def render(self, mem):
        """Return the upright picture as an array shaped (224 wide, 256 high, 3)."""
        img = np.zeros((256, 256, 3), np.uint8)               # native [y][x]
        objram = np.frombuffer(bytes(mem[0x5800:0x5880]), np.uint8)
        stars_on = mem[0x7004] & 1
        # --- starfield --------------------------------------------------
        if stars_on:
            self.star_origin = (self.star_origin + 1) % STAR_RNG_PERIOD
            idx = (self._star_base + self.star_origin) % STAR_RNG_PERIOD
            s = self.stars[idx]
            on = (s & 0x80).astype(bool) & self._star_enable
            img[on] = self.star_cols[s[on] & 0x3F]
        else:
            # the RNG keeps clocking 256*256 times per frame when disabled
            self.star_origin = (self.star_origin + 65536) % STAR_RNG_PERIOD
        # --- background tilemap -----------------------------------------
        vram = np.frombuffer(bytes(mem[0x5000:0x5400]), np.uint8).reshape(32, 32)
        tiles = self.chars[vram]                                # (32,32,8,8)
        tmap = tiles.transpose(0, 2, 1, 3).reshape(256, 256)    # [y][x]
        scroll = objram[0:64:2].astype(np.int32)
        colour = (objram[1:64:2] & 7).astype(np.int32)
        sx = np.repeat(scroll, 8)[None, :]
        pix = tmap[(self._yy + sx) & 255, self._xx]
        cx = np.repeat(colour, 8)[None, :]
        opaque = pix != 0
        pidx = (cx * 4 + pix)
        img[opaque] = self.pal[pidx[opaque]]
        # --- sprites (lower numbers have priority) -----------------------
        for n in range(7, -1, -1):
            b0, b1, b2, b3 = objram[0x40 + n * 4:0x44 + n * 4]
            sy = (240 - ((int(b0) - (1 if n < 3 else 0)) & 0xFF)) & 0xFF
            sxp = (int(b3) + 1) & 0xFF
            spr = self.sprites[b1 & 0x3F]
            if b1 & 0x40:
                spr = spr[:, ::-1]
            if b1 & 0x80:
                spr = spr[::-1, :]
            col = (b2 & 7) * 4
            y0, y1 = sy, min(sy + 16, 256)
            x0, x1 = max(sxp, 16), min(sxp + 16, 256)       # first 16 pixels clipped
            if y1 <= y0 or x1 <= x0:
                continue
            part = spr[0:y1 - y0, x0 - sxp:x1 - sxp]
            mask = part != 0
            region = img[y0:y1, x0:x1]
            region[mask] = self.pal[col + part[mask]]
        # --- bullets -----------------------------------------------------
        shells = {}
        missile_y = None
        for w in range(8):
            b1 = int(objram[0x60 + w * 4 + 1])
            y = ((0x100 - b1) if w < 3 else (0xFF - b1)) & 0xFF
            if w < 7:
                shells[y] = w
            else:
                missile_y = y
        draws = list(shells.items())
        if missile_y is not None:
            draws.append((missile_y, 7))
        for y, w in draws:
            if not 16 <= y < 240:
                continue
            x = 255 - int(objram[0x60 + w * 4 + 3])
            xs = max(x - 4, 0)
            if x > xs:
                img[y, xs:x] = self._bullet_cols[w]
        # native visible rows 16..239, rotated upright
        return img[16:240][::-1]
