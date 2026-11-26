#!/usr/bin/env python3
"""
Galaxian launcher: checks everything is in place, sets it up if not, then runs.

  1. Python 3.8 or newer?
  2. pygame + numpy importable?  If so, run straight away.
  3. Otherwise make a private environment in ./.venv (nothing is installed
     system-wide), install the requirements there, and run from it.
     If plain pygame has no build for this Python version, pygame-ce is used
     instead (a drop-in replacement with the same 'import pygame').

Any arguments are passed on to galaxian.py, e.g.  --scale 4  --lives 2
Only the standard library is used here, so it runs on a bare Python install.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.join(HERE, 'galaxian.py')
VENV = os.path.join(HERE, '.venv')
CHECK = 'import pygame, numpy'
ENV = dict(os.environ, PYGAME_HIDE_SUPPORT_PROMPT='1')


def say(msg):
    print('[galaxian] ' + msg, flush=True)


def fail(msg):
    say('PROBLEM: ' + msg)
    sys.exit(1)


def has_deps(python):
    try:
        r = subprocess.run([python, '-c', CHECK], env=ENV,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except OSError:
        return False


def venv_python():
    if os.name == 'nt':
        return os.path.join(VENV, 'Scripts', 'python.exe')
    return os.path.join(VENV, 'bin', 'python')


def pip_install(python, *pkgs):
    say('installing ' + ' '.join(pkgs) + ' ...')
    r = subprocess.run([python, '-m', 'pip', 'install', '--disable-pip-version-check', *pkgs], env=ENV)
    return r.returncode == 0


def run(python):
    say('starting Galaxian')
    r = subprocess.run([python, GAME, *sys.argv[1:]], env=ENV, cwd=HERE)
    sys.exit(r.returncode)


def main():
    if sys.version_info < (3, 8):
        fail('Python %d.%d found, but 3.8 or newer is needed. Get it from https://www.python.org/downloads/'
             % sys.version_info[:2])
    say('Python %d.%d.%d OK' % sys.version_info[:3])

    if not os.path.isfile(GAME):
        fail('galaxian.py is missing - keep the launcher in the same folder as the game files.')

    if has_deps(sys.executable):
        say('pygame and numpy OK')
        run(sys.executable)

    py = venv_python()
    if has_deps(py):
        say('pygame and numpy OK (.venv)')
        run(py)

    say('pygame/numpy not found - setting up a private environment in .venv (first run only)')
    if not os.path.isfile(py):
        r = subprocess.run([sys.executable, '-m', 'venv', VENV])
        if r.returncode != 0 or not os.path.isfile(py):
            hint = ''
            if sys.platform.startswith('linux'):
                hint = ('\n  On Debian/Ubuntu: sudo apt install python3-venv   (then run this again)'
                        '\n  Or install the packages yourself: sudo apt install python3-pygame python3-numpy')
            fail('could not create the .venv environment.' + hint)

    if not pip_install(py, 'numpy'):
        fail('installing numpy failed - check your internet connection and run this again.')
    if not pip_install(py, 'pygame'):
        say('pygame has no ready-made build for this Python - trying pygame-ce (same thing, community edition)')
        if not pip_install(py, 'pygame-ce'):
            fail('could not install pygame or pygame-ce. Check your internet connection, '
                 'or install Python 3.12 from https://www.python.org/downloads/ and try again.')

    if not has_deps(py):
        fail('the packages installed but still will not load. Delete the .venv folder and run this again.')
    say('all set')
    run(py)


if __name__ == '__main__':
    main()
