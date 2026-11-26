"""
Galaxian sound board, synthesised from the latches the game program writes.

The original board is analogue/discrete.  This follows the model used by
classic MAME's galaxian sound driver:

  $7800 pitch + $6806/$6807 volume -> 16-step "toothsaw" tone generator
                                      (music, attack whine, coin, extra life)
  $6800-$6802                       -> three background "swarm" voices
  $6004-$6007                       -> LFO rate that sweeps the swarm voices
  $6803                             -> player explosion: slow-clocked noise
                                      (deep rumble) through an RC envelope,
                                      or your own death.wav if supplied
  $6805                             -> player shot ("pew") on a rising edge
"""

import io
import os
import wave
import zipfile

import numpy as np

RATE = 44100
TOOTHSAW_LENGTH = 16
MINFREQ = 139 - 139 // 3
MAXFREQ = 139 + 139 // 3


def _tonewaves():
    amp = 64.0

    def v(r0, r1):
        return 2 * amp * r0 / (r0 + r1) - amp

    waves = np.zeros((4, TOOTHSAW_LENGTH), np.float32)
    for i in range(TOOTHSAW_LENGTH):
        r0a = r1a = r0b = r1b = 1.0 / 1e12
        if i & 1:
            r1a += 1.0 / 33000
            r1b += 1.0 / 33000
        else:
            r0a += 1.0 / 33000
            r0b += 1.0 / 33000
        if i & 4:
            r1a += 1.0 / 22000
            r1b += 1.0 / 22000
        else:
            r0a += 1.0 / 22000
            r0b += 1.0 / 22000
        waves[0][i] = v(1.0 / r0a, 1.0 / r1a)
        if i & 4:
            r1a += 1.0 / 10000
        else:
            r0a += 1.0 / 10000
        waves[1][i] = v(1.0 / r0a, 1.0 / r1a)
        if i & 8:
            r1b += 1.0 / 15000
        else:
            r0b += 1.0 / 15000
        waves[2][i] = v(1.0 / r0b, 1.0 / r1b)
        if i & 4:
            r0b += 1.0 / 10000
        else:
            r1b += 1.0 / 10000
        waves[3][i] = v(1.0 / r0b, 1.0 / r1b)
    return waves / amp


def _noise_table(n):
    out = np.zeros(n, np.float32)
    sr = 0x12345
    for i in range(n):
        bit = ((sr >> 16) ^ (sr >> 13)) & 1
        sr = ((sr << 1) | bit) & 0x1FFFF
        out[i] = 1.0 if bit else -1.0
    return out


def _shoot_wave():
    """A decaying downward sweep mixed with noise: the 555 'pew'."""
    dur = 0.28
    n = int(RATE * dur)
    t = np.arange(n) / RATE
    freq = 700 + 1900 * np.exp(-t * 14)
    phase = np.cumsum(freq) / RATE
    tone = np.sign(np.sin(2 * np.pi * phase))
    noise = _noise_table(n)
    env = np.exp(-t * 9)
    return ((0.75 * tone + 0.25 * noise) * env).astype(np.float32)


def load_wav(data, rate):
    """Decode WAV bytes (8/16-bit PCM, mono or stereo) to float32 at `rate`."""
    with wave.open(io.BytesIO(data)) as w:
        ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 1:
        a = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif width == 2:
        a = np.frombuffer(raw, '<i2').astype(np.float32) / 32768
    else:
        raise ValueError('only 8/16-bit PCM WAV files are supported')
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != rate and len(a) > 1:
        t = np.arange(int(len(a) * rate / sr)) * (sr / rate)
        a = np.interp(t, np.arange(len(a)), a)
    return a.astype(np.float32)


def find_death_sample(folders, explicit=None):
    """Look for a user-supplied explosion sample: death.wav, loose or inside a
    MAME samples zip (samples/galaxian.zip).  Returns WAV bytes or None."""
    if explicit:
        with open(explicit, 'rb') as f:
            return f.read()
    for d in folders:
        for cand in (os.path.join(d, 'samples', 'death.wav'), os.path.join(d, 'death.wav')):
            if os.path.isfile(cand):
                with open(cand, 'rb') as f:
                    return f.read()
        z = os.path.join(d, 'samples', 'galaxian.zip')
        if os.path.isfile(z):
            with zipfile.ZipFile(z) as zf:
                for name in zf.namelist():
                    if os.path.basename(name).lower() == 'death.wav':
                        return zf.read(name)
    return None


class Synth:
    def __init__(self, rate=RATE, death_sample=None):
        self.rate = rate
        self.waves = _tonewaves()
        self.tone_phase = 0.0
        self.lfo_freq = float(MAXFREQ)
        self.bg_phase = np.zeros(3)
        self.noise = _noise_table(1 << 15)
        self.noise_pos = 0.0
        self.hit_env = 0.0
        self.hit_lp = 0.0
        self.last_hit = 0
        self.death = load_wav(death_sample, rate) if death_sample else None
        self.death_pos = None if self.death is None else len(self.death)
        self.shoot = _shoot_wave()
        self.shoot_pos = len(self.shoot)
        self.last_shoot = 0

    def _lfo_period(self, m):
        """Seconds for one full MAXFREQ->MINFREQ sweep, from the 4 LFO bits."""
        r0 = 1.0 / 330000
        r1 = 1.0 / 1e12
        for bit, r in zip(range(4), (1000000, 470000, 220000, 100000)):
            if m[0x6004 + bit] & 1:
                r1 += 1.0 / r
            else:
                r0 += 1.0 / r
        r0 = 1.0 / r0
        r1 = 1.0 / r1
        rx = 100000.0 + 2000000.0 * r0 / (r0 + r1)
        return 0.639 * rx * 1e-6

    def generate(self, m, n):
        rate = self.rate
        out = np.zeros(n, np.float32)

        # --- tone generator ---------------------------------------------
        pitch = m[0x7800]
        vol = (m[0x6806] & 1) | ((m[0x6807] & 1) << 1)
        if pitch != 0xFF:
            freq = 1536000.0 / 16.0 / (256 - pitch)   # counter clock / steps
            step = freq * TOOTHSAW_LENGTH / rate
            idx = self.tone_phase + step * np.arange(1, n + 1)
            self.tone_phase = idx[-1] % TOOTHSAW_LENGTH
            out += 0.36 * self.waves[vol][(idx.astype(np.int64)) % TOOTHSAW_LENGTH]

        # --- background swarm voices --------------------------------------
        period = self._lfo_period(m)
        steps_per_sec = (MAXFREQ - MINFREQ) / period
        f = self.lfo_freq - steps_per_sec * np.arange(n) / rate
        span = MAXFREQ - MINFREQ
        f = MINFREQ + np.mod(f - MINFREQ, span)
        self.lfo_freq = MINFREQ + ((self.lfo_freq - steps_per_sec * n / rate) - MINFREQ) % span
        for v, ratio in enumerate((1.0, (100 + 2 * 300) / (100 + 2 * 470),
                                   (100 + 2 * 180) / (100 + 2 * 470))):
            ph = self.bg_phase[v] + np.cumsum(f * ratio) / rate
            self.bg_phase[v] = ph[-1] % 1.0
            if m[0x6800 + v] & 1:
                out += 0.07 * np.where((ph % 1.0) < 0.5, 1.0, -1.0)

        # --- player explosion ($6803) -----------------------------------
        hit = m[0x6803] & 1
        rising = hit and not self.last_hit
        self.last_hit = hit
        if self.death is not None:
            # user-supplied recording: start it on the rising edge, play it out
            if rising:
                self.death_pos = 0
            if self.death_pos < len(self.death):
                seg = self.death[self.death_pos:self.death_pos + n]
                out[:len(seg)] += 0.9 * seg
                self.death_pos += n
        else:
            # The board's 17-bit noise LFSR runs slowly (~960 Hz) and is latched
            # at the 2V rate, giving a deep rumble rather than hiss.  The HIT
            # latch charges a capacitor (fast attack); when the game drops HIT
            # after the explosion animation, it discharges slowly.
            t = np.arange(1, n + 1) / rate
            target = 1.0 if hit else 0.0
            tau = 0.012 if hit else 0.42
            env = target + (self.hit_env - target) * np.exp(-t / tau)
            self.hit_env = float(env[-1])
            if self.hit_env > 1e-4 or hit:
                step = 960.0 / rate
                pos = self.noise_pos + step * np.arange(n)
                self.noise_pos = (pos[-1] + step) % len(self.noise)
                raw = env * self.noise[pos.astype(np.int64) % len(self.noise)]
                # one-pole low-pass (~1.1 kHz) for the board's amp/speaker
                k = 1.0 - np.exp(-2 * np.pi * 1100.0 / rate)
                y = self.hit_lp
                filt = np.empty(n, np.float32)
                for i in range(n):
                    y += k * (raw[i] - y)
                    filt[i] = y
                self.hit_lp = y
                out += 1.25 * filt
            else:
                self.hit_env = 0.0

        # --- player shot --------------------------------------------------
        s = m[0x6805] & 1
        if s and not self.last_shoot:
            self.shoot_pos = 0
        self.last_shoot = s
        if self.shoot_pos < len(self.shoot):
            seg = self.shoot[self.shoot_pos:self.shoot_pos + n]
            out[:len(seg)] += 0.45 * seg
            self.shoot_pos += n

        return np.clip(out * 0.6, -1.0, 1.0)
