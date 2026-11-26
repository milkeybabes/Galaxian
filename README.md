# Galaxian (Namco, 1979) - Python port

A hand port of the arcade game's Z80 program to Python. It is a port, not an emulator: no
CPU is emulated. Each routine in `game.py` is a translation of the original routine,
named after the label in Scott Tunstall's commented disassembly
(seanriddle.com/galaxian.asm), with its address noted.

To keep the gameplay identical, the game state uses the original memory layout. That
preserves all of the original's behaviour, including:

* the swarm march and its edge limits
* the swarm halting when your shot comes near
* flank selection and attack timers
* flagship escorts and the 150/200/300/800 bonus
* the "shocked swarm" pause after a flagship kill
* aggressive aliens when 3 or fewer remain
* surviving flagships carried into the next wave
* difficulty ramps, and the 14-bullet multiplexing (visible as 30 Hz shot flicker)

## Running it

Use the launcher for your system. It checks for Python 3.8+, pygame and numpy. If anything is
missing, it sets them up in a private `.venv` folder inside the game folder; nothing is installed
system-wide. On later runs it starts straight away.

| System | How |
|---|---|
| Windows | double-click `run_galaxian.bat` |
| Mac | double-click `run_galaxian.command` (first time: right-click > Open > Open) |
| Linux | `./run_galaxian.sh` |

The only thing you need beforehand is Python 3 from [python.org](https://www.python.org/downloads/).
On Windows, tick *Add python.exe to PATH* when you install it. The first run needs an internet
connection to fetch pygame and numpy.

Options pass straight through, e.g. `run_galaxian.bat --scale 4 --lives 2`.

Or run it by hand:

```
pip install pygame numpy
python galaxian.py
```

No ROM files are needed. The graphics, colours and data tables are built into the code
(`gfxdata.py` and `romdata.py`).

## Keys

| Key | Action |
|---|---|
| 5 | Insert coin |
| 1 / 2 | 1-player / 2-player start |
| Left / Right (or A / D) | Move |
| Space / Ctrl | Fire |
| F3 | Reset (power cycle, including the boot test screens) |
| F5 | Pause / unpause |
| F12 | Screenshot (saved to `screenshots/`) |
| B | Show / hide the cabinet bezel (when one is loaded) |
| F11 or Alt+Enter | Fullscreen on / off |
| Esc | Quit |

## DIP switches (command line)

| Option | Values |
|---|---|
| `--lives` | `2` or `3` (default 3) |
| `--bonus` | `7000` (default), `10000`, `12000` or `20000` |
| `--coinage` | `1C1C` (default), `2C1C`, `1C2C` or `FREE` |

Other options: `--fullscreen`, `--mute`, `--death-sample FILE.wav`.

## Optional: cabinet bezel

You can show the game inside cabinet artwork, such as a PNG from The Bezel Project, for that
arcade look. Use any image with a transparent screen opening; the game finds the opening by
itself and fits the picture into it. Galaxian's monitor was 3:4, so in bezel mode the picture is
stretched to 3:4 just as it was on the real screen.

* Pass it on the command line: `--bezel Screen.png`
* or name it `bezel.png` and put it next to `galaxian.py` (or in `bezels/galaxian.png`), and it
  loads automatically.

Press **B** to switch between the bezel and the plain game, and **F11** (or Alt+Enter) for
fullscreen. On a 1920x1080 display a 1920x1080 bezel is shown at exactly 1:1, pixel for pixel;
other resolutions scale it to fit and keep its shape. No artwork is included with the game; use your own file.

## Optional: the explosion sample

The player explosion is synthesised from the circuit by default. If you have MAME's Galaxian
samples, the game will play that recording instead. It looks for `death.wav` in any of these
places:

* `samples/death.wav`
* `death.wav` next to `galaxian.py`
* inside `samples/galaxian.zip` (MAME's samples layout)

You can also point to a file with `--death-sample`.

## Files

| File | Contents |
|---|---|
| `game.py` | Game logic: attract mode, scripts, swarm, aliens, bullets, scoring, coin handling, sound control |
| `video.py` | Tilemap with per-column scroll and colour, sprites, bullets, LFSR starfield |
| `sound.py` | Synthesis of the discrete sound board from the latches the game writes |
| `romdata.py` | Data tables from the original program, at their original addresses |
| `gfxdata.py` | Original character/sprite graphics and colour PROM data |
| `galaxian.py` | pygame front end |
| `launcher.py` + `run_galaxian.*` | Requirement check, setup and launch |

## Notes

* The original sound hardware is analogue. `sound.py` follows classic MAME's model of
  it, so it sounds close to the arcade but isn't a circuit-level simulation. The
  timing and sequencing come from the game code, so they match the original.
* Upright cabinet only. In a 2-player game, player 2 uses the same controls.

## Bonus: Galaxian Flight Patterns

Ever wondered exactly how the Galaxians swoop? [Galaxian Flight Patterns](flight_patterns.md) draws
their flights from the game's own code. Every dotted path in it was recorded frame by frame from
this port. It covers:

- the 47-step arc the aliens climb out of the convoy on
- how each dive is aimed at where you're standing
- the clever "Minsky circle" trick that makes smooth sine-wave swoops on a 1979 chip with no
  multiply instruction
- how flagships and their escorts fly in formation
- the endgame, where the last few aliens stop going home and start looping the loop

It's a look under the hood of one of the games that taught enemies to break formation, and it's
a good read if you love arcade history or just want to know why you kept getting hit.