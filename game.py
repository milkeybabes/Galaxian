"""
Galaxian (Namco 1979) - game logic, hand-ported to Python from the Z80 program.

Reference: Scott Tunstall's commented disassembly (seanriddle.com/galaxian.asm),
verified byte-for-byte against the MAME parent set "galaxian"
(galmidw.u/v/w/y + 7l).

Design notes
------------
* All game state lives in a 64K byte array laid out exactly like the arcade
  board's memory map ($4000 work RAM, $5000 character RAM, $5800 object RAM,
  $6000-$7800 output latches).  Keeping the original layout is what lets the
  port reproduce the original's behaviour exactly - including its 8-bit
  wraparounds, shared counters and the occasional bug - while still being
  plain Python (no CPU emulation: every routine below is a translation).
* Each method is named after the routine label in the disassembly and carries
  its original address, so it can be checked side by side with the listing.
* frame() is the vertical-blank NMI handler; run_main_loop() is the code at
  $200A that the CPU runs between interrupts (it drains the command queue and
  animates the swarm).
"""

from romdata import ROM

# --------------------------------------------------------------------------
# RAM addresses (names from the disassembly)
# --------------------------------------------------------------------------
DIP_SWITCH_1_2_STATE = 0x4000
COIN_COUNT = 0x4001
NUM_CREDITS = 0x4002
COIN_CONTROL = 0x4003
UNPROCESSED_COINS = 0x4004
SCRIPT_NUMBER = 0x4005
IS_GAME_IN_PLAY = 0x4006
IS_GAME_OVER = 0x4007
TEMP_COUNTER_1 = 0x4008
TEMP_COUNTER_2 = 0x4009
SCRIPT_STAGE = 0x400A
TEMP_CHAR_RAM_PTR = 0x400B
CURRENT_PLAYER = 0x400D
IS_TWO_PLAYER_GAME = 0x400E
IS_COCKTAIL = 0x400F
PORT_STATE_6000 = 0x4010
PORT_STATE_6800 = 0x4011
PORT_STATE_7000 = 0x4012
PREV_PORT_STATE_6000 = 0x4013
PREV_PORT_STATE_6800 = 0x4014
PREV_PREV_PORT_STATE_6000 = 0x4015
PREV_PREV_PREV_STATE_6000 = 0x4016
DISPLAY_IS_COCKTAIL_P2 = 0x4018
PUSH_START_BUTTON_COUNTER = 0x4019
DIAGNOSTIC_MESSAGE_TYPE = 0x401A
RAND_NUMBER = 0x401E
DIP_SWITCH_5_STATE = 0x401F
OBJRAM_BACK_BUF = 0x4020
CIRC_CMD_QUEUE_PTR_LO = 0x40A0
CIRC_CMD_QUEUE_PROC_LO = 0x40A1
PLAYER_ONE_SCORE = 0x40A2
PLAYER_TWO_SCORE = 0x40A5
HI_SCORE = 0x40A8
CAN_BLINK_1UP_2UP = 0x40AB
BONUS_GALIXIP_FOR = 0x40AC
IS_COLUMN_SCROLLING = 0x40B0
ALIEN_SWARM_FLAGS = 0x4100
SOUND_VOL = 0x41C0
PITCH_SOUND_FX_BASE_FREQ = 0x41C1
ENABLE_ALIEN_ATTACK_SOUND = 0x41C2
PLAY_EXTRA_LIFE_SOUND = 0x41C7
PLAY_PLAYER_CREDIT_SOUND = 0x41C9
PLAY_PLAYER_SHOOT_SOUND = 0x41CC
IS_COMPLEX_SOUND_PLAYING = 0x41CD
RESET_SWARM_SOUND_TEMPO = 0x41D0
PLAY_GAME_START_MELODY = 0x41D1
ALIEN_DEATH_SOUND = 0x41DF
HAS_PLAYER_SPAWNED = 0x4200
IS_PLAYER_DYING = 0x4201
PLAYER_Y = 0x4202
IS_PLAYER_HIT = 0x4204
HAS_PLAYER_BULLET_BEEN_FIRED = 0x4208
PLAYER_BULLET_X = 0x4209
PLAYER_BULLET_Y = 0x420A
IS_PLAYER_BULLET_DONE = 0x420B
SWARM_DIRECTION = 0x420D
SWARM_SCROLL_VALUE = 0x420E
SWARM_SCROLL_MAX_EXTENTS = 0x4210
CURRENT_PLAYER_STATE = 0x4218
PLAYER_LIVES = 0x421D
TIMING_VARIABLE = 0x425F
ENEMY_BULLETS = 0x4260
INFLIGHT_ALIENS = 0x42B0

# Output latches (writes).  Inputs are supplied separately (in0/in1/in2).
PORT_STARS_ON = 0x7004
PORT_HFLIP = 0x7006
PORT_VFLIP = 0x7007
PORT_PITCH = 0x7800


def rrc(a):
    return ((a >> 1) | (a << 7)) & 0xFF


def rlc(a):
    return ((a << 1) | (a >> 7)) & 0xFF


def swap(a):
    return ((a >> 4) | (a << 4)) & 0xFF


def bcd_to_int(v):
    return (v >> 4) * 10 + (v & 0x0F)


def int_to_bcd(v):
    return ((v // 10) << 4) | (v % 10)


class Galaxian:
    """The arcade program.  Call frame() once per vertical blank (60.6 Hz)."""

    def __init__(self, dips=None):
        # in0 ($6000): b0 coin1, b1 coin2, b2 left, b3 right, b4 fire,
        #              b5 cabinet (0=upright), b6 test, b7 service
        # in1 ($6800): b0 start1, b1 start2, b2..4 p2 controls, b6/b7 coinage DIP
        # in2 ($7000): b0/b1 bonus life DIP, b2 lives DIP (1 = 3 ships)
        self.in0 = 0
        self.in1 = 0
        self.in2 = 0x04
        if dips:
            self.set_dips(**dips)
        self.power_on()

    # ------------------------------------------------------------------
    # DIP switches (same meaning as the operator manual / MAME)
    # ------------------------------------------------------------------
    def set_dips(self, coinage=0, bonus=0, lives=3):
        """coinage: 0=1C1C 1=2C1C 2=1C2C 3=free play;
        bonus: 0=7000 1=10000 2=12000 3=20000; lives: 2 or 3."""
        self.coinage = coinage & 3
        self.in2 = (bonus & 3) | (0x04 if lives == 3 else 0)

    def _in1(self):
        return (self.in1 & 0x3F) | (getattr(self, 'coinage', 0) << 6)

    # ------------------------------------------------------------------
    # memory helpers
    # ------------------------------------------------------------------
    def rw(self, a):
        m = self.m
        return m[a] | (m[a + 1] << 8)

    def ww(self, a, v):
        self.m[a] = v & 0xFF
        self.m[a + 1] = (v >> 8) & 0xFF

    def fill(self, a, n, v):
        """RST $10: fill n bytes (n=0 means 256)."""
        if n == 0:
            n = 256
        self.m[a:a + n] = bytes([v & 0xFF]) * n
        return a + n

    def rand(self):
        """$003C GENERATE_RANDOM_NUMBER"""
        m = self.m
        r = (m[RAND_NUMBER] * 5 + 1) & 0xFF
        m[RAND_NUMBER] = r
        return r

    # ------------------------------------------------------------------
    # Power on / reset  ($0000 -> $1A55 -> $1B8A -> $2000)
    # ------------------------------------------------------------------
    def power_on(self):
        self.m = m = bytearray(0x10000)
        # $1A55: clear the screen, attributes, ports
        m[0x5000:0x5400] = b'\x10' * 0x400
        m[0x5800:0x5900] = bytes(0x100)
        m[0x6004:0x6008] = b'\x01' * 4
        m[PORT_PITCH] = 0xFF
        # (RAM / ROM self tests always pass here)
        # $1B8A ALL_TESTS_HAVE_PASSED
        self.fill(0x4000, 0xC0, 0)
        self.fill(0x40C0, 0x40, 0xFF)
        self.fill(0x4100, 0x100, 0)
        self.fill(0x4200, 0x100, 0)
        self.fill(0x4300, 0xA0, 0)
        m[PORT_HFLIP] = m[PORT_VFLIP] = 0
        m[DISPLAY_IS_COCKTAIL_P2] = 0
        m[TEMP_COUNTER_1] = 0x20
        m[DIAGNOSTIC_MESSAGE_TYPE] = 3
        m[CIRC_CMD_QUEUE_PTR_LO] = 0xC0
        m[CIRC_CMD_QUEUE_PROC_LO] = 0xC0
        m[PORT_STARS_ON] = 1
        # $2000: reset player scores / high score
        self.fill(0x40A2, 0x1E, 0)
        self.frame_count = 0

    # ------------------------------------------------------------------
    # $0066 NMI handler - one call per video frame
    # ------------------------------------------------------------------
    def frame(self):
        m = self.m
        self.frame_count += 1
        if m[DIAGNOSTIC_MESSAGE_TYPE]:
            self.handle_diagnostics()
        else:
            # $0079 copy back buffer to the hardware object RAM
            m[0x5800:0x5880] = m[0x4020:0x40A0]
            # $0087 read ports
            m[PREV_PREV_PREV_STATE_6000] = m[PREV_PREV_PORT_STATE_6000]
            m[PREV_PREV_PORT_STATE_6000] = m[PREV_PORT_STATE_6000]
            m[PREV_PORT_STATE_6000] = m[PORT_STATE_6000]
            m[PREV_PORT_STATE_6800] = m[PORT_STATE_6800]
            m[PORT_STATE_7000] = self.in2
            m[PORT_STATE_6800] = self._in1()
            m[PORT_STATE_6000] = self.in0 & 0xBF       # TEST switch never pressed
            m[TIMING_VARIABLE] = (m[TIMING_VARIABLE] - 1) & 0xFF
            self.check_if_coin_inserted()
            self.handle_unprocessed_coins()
            self.handle_coin_lockout()
            self.handle_sound()
            self.handle_swarm_sound()
            self.handle_text_scroll()
            s = m[SCRIPT_NUMBER]
            if s == 0:
                self.script_zero()
            elif s == 1:
                self.script_one()
            elif s == 2:
                self.script_two()
            elif s == 3:
                self.script_three()
            elif s == 4:
                self.script_four()
        # what the main CPU loop does between NMIs
        self.run_main_loop()

    # ------------------------------------------------------------------
    # $200A main loop: drain the circular command queue, animate swarm
    # ------------------------------------------------------------------
    def run_main_loop(self):
        m = self.m
        for _ in range(64):
            lo = m[CIRC_CMD_QUEUE_PROC_LO]
            cmd = m[0x4000 | lo]
            if cmd & 0x80:
                break
            m[0x4000 | lo] = 0xFF
            lo = (lo + 1) & 0xFF
            param = m[0x4000 | lo]
            m[0x4000 | lo] = 0xFF
            lo = (lo + 1) & 0xFF
            if lo < 0xC0:
                lo = 0xC0
            m[CIRC_CMD_QUEUE_PROC_LO] = lo
            c = cmd & 7
            if c == 0:
                self.draw_alien_command(param)
            elif c == 1:
                self.delete_alien_command(param)
            elif c == 2:
                self.display_player_command(param)
            elif c == 3:
                self.update_player_score_command(param)
            elif c == 4:
                self.reset_score_command(param)
            elif c == 5:
                self.display_score_command(param)
            elif c == 6:
                self.print_text(param)
            else:
                self.display_bottom_of_screen(param)
        self.handle_swarm_animation()

    def queue_command(self, d, e):
        """$08F2 QUEUE_COMMAND"""
        m = self.m
        lo = m[CIRC_CMD_QUEUE_PTR_LO]
        if not (m[0x4000 | lo] & 0x80):
            return
        m[0x4000 | lo] = d
        lo = (lo + 1) & 0xFF
        m[0x4000 | lo] = e & 0xFF
        lo = (lo + 1) & 0xFF
        if lo < 0xC0:
            lo = 0xC0
        m[CIRC_CMD_QUEUE_PTR_LO] = lo

    # ==================================================================
    # Diagnostics shown at power on ($1BCD)
    # ==================================================================
    def handle_diagnostics(self):
        m = self.m
        t = m[DIAGNOSTIC_MESSAGE_TYPE]
        if t == 1:
            self.handle_service_mode()
        elif t == 2:
            self.character_ram_colour_test()
        elif t == 3:
            self.objram_test()
        else:
            self.power_on()

    def objram_test(self):
        """$1BDD - fills object RAM with test patterns (the boot-up garbage)."""
        m = self.m
        a = m[RAND_NUMBER]
        for i in range(256):
            m[0x5800 + i] = a
            a = (a + 0x2F) & 0xFF
        self.rand()
        m[TEMP_COUNTER_1] = (m[TEMP_COUNTER_1] - 1) & 0xFF
        if m[TEMP_COUNTER_1]:
            return
        m[0x5800:0x5900] = bytes(0x100)
        m[IS_GAME_IN_PLAY] = 1
        m[DIAGNOSTIC_MESSAGE_TYPE] = 1
        m[0x4226] = 1
        m[TIMING_VARIABLE] = 1
        m[0x4238] = 1
        m[0x5213] = 0x1F      # "O"
        m[0x51F3] = 0x1B      # "K"

    def handle_service_mode(self):
        """$1C3A - displays the DIP switch settings (seen for one frame)."""
        m = self.m
        self.handle_sound()
        self.legacy_16a6()
        in0, in1, in2 = self.in0, self._in1(), self.in2
        self.display_dip_switch_settings((in1 >> 6) & 3)
        self.display_dip_switch_settings((in2 & 3) + 4)
        self.display_dip_switch_settings(((in2 >> 2) & 1) + 8)
        if in0 & 0x40:
            return
        m[IS_GAME_IN_PLAY] = 0
        m[DIAGNOSTIC_MESSAGE_TYPE] = 2
        m[TEMP_COUNTER_1] = 0x10
        m[TEMP_COUNTER_2] = 0x30
        self.ww(TEMP_CHAR_RAM_PTR, 0x5000)
        m[0x6000:0x6004] = bytes(4)
        self.reset_sound()

    def reset_sound(self):
        """$1CB5 RESET_SOUND"""
        m = self.m
        m[0x6004:0x6008] = b'\x01' * 4
        m[0x6800:0x6808] = bytes(8)
        m[0x7001:0x7006] = bytes(5)   # NB: this also turns the stars off
        m[PORT_PITCH] = 0xFF

    def display_dip_switch_settings(self, a):
        """$1CCF"""
        p = 0x1CF6 + a * 5
        text = ROM.word(p)
        addr = ROM.word(p + 2)
        n = ROM[p + 4]
        for i in range(n):
            self.m[addr] = (ROM[text + i] - 0x30) & 0xFF
            addr -= 32

    def character_ram_colour_test(self):
        """$1D28"""
        m = self.m
        if m[TEMP_COUNTER_1]:
            p = self.rw(TEMP_CHAR_RAM_PTR)
            for _ in range(16):
                m[p] = 0x30
                m[p + 1] = 0x32
                p += 2
            for _ in range(16):
                m[p] = 0x34
                m[p + 1] = 0x36
                p += 2
            self.ww(TEMP_CHAR_RAM_PTR, p)
            m[TEMP_COUNTER_1] -= 1
            if m[TEMP_COUNTER_1]:
                return
        if m[TEMP_COUNTER_2]:
            m[TEMP_COUNTER_2] -= 1
            if m[TEMP_COUNTER_2]:
                return
        if self.in0 & 0x40:
            return
        self.ww(TEMP_CHAR_RAM_PTR, 0x5000)
        m[TEMP_COUNTER_1] = 0x20
        m[DIAGNOSTIC_MESSAGE_TYPE] = 0
        m[SCRIPT_NUMBER] = 0

    # ==================================================================
    # SCRIPT ZERO ($00E6): clear the screen after power on
    # ==================================================================
    def script_zero(self):
        m = self.m
        hl = self.rw(TEMP_CHAR_RAM_PTR)
        hl = self.fill(hl, 0x20, 0x10)
        self.ww(TEMP_CHAR_RAM_PTR, hl)
        m[TEMP_COUNTER_1] = (m[TEMP_COUNTER_1] - 1) & 0xFF
        if m[TEMP_COUNTER_1]:
            return
        m[IS_GAME_OVER] = 1
        m[IS_GAME_IN_PLAY] = 0
        m[SCRIPT_NUMBER] = 1
        m[SCRIPT_STAGE] = 0
        m[DIP_SWITCH_1_2_STATE] = (m[PORT_STATE_6800] >> 6) & 3
        m[DIP_SWITCH_5_STATE] = (m[PORT_STATE_7000] & 4) >> 2
        self.unpack_alien_swarm(0x051B, rom=True)
        m[IS_COCKTAIL] = (m[PORT_STATE_6000] >> 5) & 1
        m[BONUS_GALIXIP_FOR] = ROM[0x0152 + (self.in2 & 3)]
        self.set_colour_attributes(0x1D71)
        m[0x5340] = 0x01
        m[0x5320] = 0x25
        m[0x5300] = 0x20
        self.queue_command(6, 4)
        self.queue_command(5, 3)

    # ==================================================================
    # SCRIPT ONE ($0156): attract mode
    # ==================================================================
    def script_one(self):
        m = self.m
        self.handle_swarm_movement()
        self.set_alien_presence_flags()
        stage = m[SCRIPT_STAGE]
        (self.display_game_over_and_remaining_credit_1,     # 0  $018C
         self.set_push_start_button_counter,                # 1  $01BE
         self.hide_swarm_and_prepare_to_clear_screen,       # 2  $01C6
         self.clear_screen_before_we_are_the_galaxians,     # 3  $01E1
         self.display_we_are_the_galaxians_intro,           # 4  $0218
         self.scroll_on_convoy_charger_points,              # 5  $023F
         self.display_namco_logo,                           # 6  $0267
         self.blink_convoy_charger_points,                  # 7  $028E
         self.hide_swarm_and_prepare_to_clear_screen,       # 8  $01C6
         self.clear_we_are_galaxians_screen_and_game_over,  # 9  $029D
         self.display_game_over_and_remaining_credit_2,     # 10 $02D1
         self.wait_for_temp_counter_2,                      # 11 $032E
         self.clear_alien_swarm_and_suspend_animation,      # 12 $02E8
         self.create_attract_mode_alien_swarm,              # 13 $02FD
         self.handle_spawn_player,                          # 14 $0614
         self.handle_main_game_logic,                       # 15 $0661
         self.handle_player_one_killed,                     # 16 $06D8
         self.wait_for_temp_counter_2,                      # 17 $032E
         self.set_script_stage_to_1,                        # 18 $0322
         self.power_on,                                     # 19 ($0000)
         )[stage]()
        # $03D7 - credit inserted? go to script two
        if m[SCRIPT_NUMBER] != 1 or m[NUM_CREDITS] == 0:
            return
        m[SCRIPT_NUMBER] = 2
        m[IS_GAME_OVER] = 0
        m[SCRIPT_STAGE] = 0
        m[ENABLE_ALIEN_ATTACK_SOUND] = 0
        m[ALIEN_DEATH_SOUND] = 0
        m[IS_COLUMN_SCROLLING] = 0

    def display_game_over_and_remaining_credit_1(self):     # $018C
        m = self.m
        self.queue_command(7, 1)
        self.queue_command(6, 0)
        m[IS_GAME_OVER] = 1
        m[PORT_STARS_ON] = 1
        m[SCRIPT_STAGE] += 1
        m[PUSH_START_BUTTON_COUNTER] = 0
        m[CURRENT_PLAYER] = 0
        m[IS_TWO_PLAYER_GAME] = 0
        m[IS_GAME_IN_PLAY] = 0
        m[TEMP_COUNTER_1] = 0x60
        m[TEMP_COUNTER_2] = 0x10

    def set_push_start_button_counter(self):                # $01BE
        self.m[PUSH_START_BUTTON_COUNTER] = 1
        self.wait_for_temp_counters()

    def hide_swarm_and_prepare_to_clear_screen(self):       # $01C6
        m = self.m
        self.fill(ALIEN_SWARM_FLAGS, 0x80, 0)
        m[TIMING_VARIABLE] = 0
        m[0x4224] = 0
        self.ww(TEMP_CHAR_RAM_PTR, 0x5002)
        m[TEMP_COUNTER_2] = 0x20
        m[SCRIPT_STAGE] += 1

    def clear_screen_before_we_are_the_galaxians(self):     # $01E1
        m = self.m
        hl = self.fill(self.rw(TEMP_CHAR_RAM_PTR), 0x1C, 0x10) + 4
        self.ww(TEMP_CHAR_RAM_PTR, hl)
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[SCRIPT_STAGE] += 1
        m[TEMP_COUNTER_1] = 0x40
        m[TEMP_COUNTER_2] = 0x04
        self.fill(0x4200, 0x30, 0)
        m[PORT_HFLIP] = m[PORT_VFLIP] = 0
        m[DISPLAY_IS_COCKTAIL_P2] = 0
        m[0x4238] = 1
        self.set_colour_attributes(0x1DB1)

    def display_we_are_the_galaxians_intro(self):           # $0218
        m = self.m
        self.set_swarm_scroll_offset(0)
        m[TEMP_COUNTER_1] = (m[TEMP_COUNTER_1] - 1) & 0xFF
        if m[TEMP_COUNTER_1]:
            return
        m[TEMP_COUNTER_1] = 0x50
        self.queue_command(6, m[TEMP_COUNTER_2] + 6)
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[SCRIPT_STAGE] += 1
        m[TEMP_COUNTER_1] = 0x20
        m[TEMP_COUNTER_2] = 0x04
        self.fill(INFLIGHT_ALIENS, 0, 0)
        m[0x4241] = 0

    def scroll_on_convoy_charger_points(self):              # $023F
        m = self.m
        self.set_swarm_scroll_offset(0)
        self.handle_inflight_alien_sprite_update()
        self.handle_inflight_aliens()
        self.handle_draw_convoy_charger_points()
        m[TEMP_COUNTER_1] = (m[TEMP_COUNTER_1] - 1) & 0xFF
        if m[TEMP_COUNTER_1]:
            return
        m[TEMP_COUNTER_1] = 0xD2
        self.init_convoy_charger_sprite()
        m[0x4241] = (m[0x4241] + 1) & 0xFF
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[TEMP_COUNTER_2] = 0xD2
        m[SCRIPT_STAGE] += 1
        m[0x4058] = 0

    def display_namco_logo(self):                           # $0267
        m = self.m
        self.set_swarm_scroll_offset(0)
        self.handle_inflight_alien_sprite_update()
        self.handle_inflight_aliens()
        self.handle_draw_convoy_charger_points()
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[SCRIPT_STAGE] += 1
        m[0x4058] = 0
        m[TEMP_COUNTER_1] = 0x40
        m[TEMP_COUNTER_2] = 0x11
        m[0x4241] = (m[0x4241] + 1) & 0xFF
        self.queue_command(6, 0x0F)

    def blink_convoy_charger_points(self):                  # $028E
        self.set_swarm_scroll_offset(0)
        self.handle_inflight_alien_sprite_update()
        self.handle_inflight_aliens()
        self.handle_draw_convoy_charger_points()
        self.wait_for_temp_counters()

    def clear_we_are_galaxians_screen_and_game_over(self):  # $029D
        m = self.m
        self.set_swarm_scroll_offset(0)
        hl = self.fill(self.rw(TEMP_CHAR_RAM_PTR), 0x1C, 0x10) + 4
        self.ww(TEMP_CHAR_RAM_PTR, hl)
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[SCRIPT_STAGE] += 1
        self.fill(INFLIGHT_ALIENS, 0, 0)
        self.fill(0x4060, 0x40, 0)
        m[TEMP_COUNTER_1] = 0x40
        m[TEMP_COUNTER_2] = 0x04
        self.set_colour_attributes(0x1D71)
        self.queue_command(6, 0)

    def display_game_over_and_remaining_credit_2(self):     # $02D1
        m = self.m
        self.queue_command(7, 1)
        self.queue_command(6, 0)
        m[SCRIPT_STAGE] += 1
        m[TEMP_COUNTER_1] = 0x60
        m[TEMP_COUNTER_2] = 0x10

    def clear_alien_swarm_and_suspend_animation(self):      # $02E8
        m = self.m
        self.fill(ALIEN_SWARM_FLAGS, 0x80, 0)
        m[TIMING_VARIABLE] = 0
        m[0x4238] = 0
        m[TEMP_COUNTER_2] = 0x40
        m[SCRIPT_STAGE] += 1                 # $0593 ...
        self.set_colour_attributes(0x1D71)   # ... falls into $0595

    def create_attract_mode_alien_swarm(self):              # $02FD
        m = self.m
        self.unpack_alien_swarm(0x051B, rom=True)
        for i in range(8):
            m[CURRENT_PLAYER_STATE + i] = ROM[0x052B + i]
        m[TIMING_VARIABLE] = 0
        m[PLAYER_LIVES] = 1
        m[SCRIPT_STAGE] += 1
        m[TEMP_CHAR_RAM_PTR] = 0x96          # (sic - original writes $400B)
        m[0x4245] = 0x40
        m[0x4246] = 0x06

    def set_script_stage_to_1(self):                        # $0322
        m = self.m
        m[SCRIPT_STAGE] = 1
        m[TEMP_COUNTER_1] = 0x03
        m[TEMP_COUNTER_2] = 0x03

    def wait_for_temp_counter_2(self):                      # $032E
        m = self.m
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[SCRIPT_STAGE] += 1

    def wait_for_temp_counters(self):                       # $0336
        m = self.m
        m[TEMP_COUNTER_1] = (m[TEMP_COUNTER_1] - 1) & 0xFF
        if m[TEMP_COUNTER_1]:
            return
        m[TEMP_COUNTER_1] = 0x3C
        self.wait_for_temp_counter_2()

    def init_convoy_charger_sprite(self):                   # $0341
        m = self.m
        b = (m[TEMP_COUNTER_2] - 1) & 0xFF
        ix = 0x4330 + rrc(rrc(rrc(b)))
        m[ix] = 1
        m[ix + 1] = 0
        m[ix + 2] = 0x0D
        m[ix + 4] = 0
        m[ix + 5] = 0x0C
        m[ix + 7] = b

    def handle_draw_convoy_charger_points(self):            # $0367
        m = self.m
        a = m[0x4241]
        if a == 0:
            return
        a -= 1
        if a == 0:
            return
        b = a
        t = m[TIMING_VARIABLE]
        if (t & 0x3F) == 0:
            # $03C0 CLEAR_DEMO_CONVOY_CHARGER_POINTS
            hl = 0x5193
            while True:
                for _ in range(3):
                    m[hl] = 0x10
                    hl = (hl - 32) & 0xFFFF
                hl = (hl & 0xFF00) | ((hl + 0x62) & 0xFF)
                b = (b - 1) & 0xFF
                if b == 0:
                    return
        if (t & 0x3F) != 0x20:
            return
        idx = rlc(rlc(t)) & 3
        src = 0x039A + idx * 3
        de = 0x5193
        de = self.draw_3_characters(src, de)
        b -= 1
        if b == 0:
            return
        src = 0x03A6
        while True:
            de = self.draw_3_characters(src, de)
            src += 3
            b = (b - 1) & 0xFF
            if b == 0:
                return

    def draw_3_characters(self, src, de):                   # $03AF
        m = self.m
        e = de & 0xFF
        for i in range(3):
            m[(de & 0xFF00) | e] = ROM[src + i]
            e = (e - 0x20) & 0xFF
        e = (e + 0x62) & 0xFF
        return (de & 0xFF00) | e

    # ==================================================================
    # SCRIPT TWO ($03F2): credit inserted - wait for start button
    # ==================================================================
    def script_two(self):
        m = self.m
        self.handle_swarm_movement()
        self.set_alien_presence_flags()
        (self.init_0408,
         self.wait_before_displaying_push_start_button,
         self.display_push_start_button_and_bonus,
         self.blink_lamps_if_credit_inserted)[m[SCRIPT_STAGE]]()
        self.handle_start_buttons()

    def init_0408(self):                                    # $0408
        m = self.m
        self.set_colour_attributes(0x1D91)
        self.fill(0x4060, 0x40, 0)
        self.fill(0x4260, 0x100, 0)
        self.fill(0x4360, 0x50, 0)
        m[0x4238] = 0
        m[IS_COLUMN_SCROLLING] = 0
        self.ww(TEMP_CHAR_RAM_PTR, 0x5002)
        m[TEMP_COUNTER_2] = 0x10
        m[SCRIPT_STAGE] += 1

    def wait_before_displaying_push_start_button(self):     # $0430
        m = self.m
        m[PUSH_START_BUTTON_COUNTER] = (m[PUSH_START_BUTTON_COUNTER] - 1) & 0xFF
        if m[PUSH_START_BUTTON_COUNTER]:
            self.blink_lamps_if_credit_inserted()
            return
        m[SCRIPT_STAGE] += 1
        self.fill(ALIEN_SWARM_FLAGS, 0x80, 0)

    def display_push_start_button_and_bonus(self):          # $0443
        m = self.m
        hl = self.fill(self.rw(TEMP_CHAR_RAM_PTR), 0x1C, 0x10) + 4
        hl = self.fill(hl, 0x1C, 0x10) + 4
        self.ww(TEMP_CHAR_RAM_PTR, hl)
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[SCRIPT_STAGE] += 1
        m[PORT_HFLIP] = m[PORT_VFLIP] = 0
        m[DISPLAY_IS_COCKTAIL_P2] = 0
        self.queue_command(7, 2)
        self.queue_command(6, 1)
        self.blink_lamps_if_credit_inserted()

    def blink_lamps_if_credit_inserted(self):               # $0473
        m = self.m
        if not (m[TIMING_VARIABLE] & 0x20):
            m[0x6000] = m[0x6001] = 0
            return
        c = m[NUM_CREDITS]
        if c == 0:
            return
        m[0x6000] = 1
        if c > 1:
            m[0x6001] = 1

    def handle_start_buttons(self):                         # $0492
        m = self.m
        a = m[PORT_STATE_6800]
        if a & 1:
            # $04F2 1P start
            if m[NUM_CREDITS] == 0:
                m[SCRIPT_NUMBER] = 1
                return
            m[NUM_CREDITS] -= 1
            self.fill(0x41A0, 0x20, 0)
            m[CURRENT_PLAYER] = 0
            m[IS_TWO_PLAYER_GAME] = 0
        elif a & 2:
            # $049C 2P start
            if m[NUM_CREDITS] < 2:
                return
            m[NUM_CREDITS] -= 2
            for i in range(0x20):
                m[0x41A0 + i] = ROM[0x051B + i]
            if m[DIP_SWITCH_5_STATE] & 1:
                m[0x41B5] = 3
            m[CURRENT_PLAYER] = 0
            m[IS_TWO_PLAYER_GAME] = 1
        else:
            return
        # $04BF
        for i in range(0x20):
            m[0x4180 + i] = ROM[0x051B + i]
        if m[DIP_SWITCH_5_STATE] & 1:
            m[0x4195] = 3
        m[SCRIPT_STAGE] = 0
        m[SCRIPT_NUMBER] = 3
        m[IS_GAME_IN_PLAY] = 1
        m[PLAY_GAME_START_MELODY] = 1
        self.queue_command(6, 4)
        self.queue_command(4, 0)
        self.queue_command(4, 1)

    # ==================================================================
    # SCRIPT THREE ($0536) / FOUR ($077B): player one / player two
    # ==================================================================
    def script_three(self):
        m = self.m
        self.handle_swarm_movement()
        self.set_alien_presence_flags()
        (self.reset_for_turn_0550,
         self.clear_row_of_screen,
         self.player_one_init,
         self.clear_player_text,
         self.handle_spawn_player,
         self.handle_main_game_logic,
         self.handle_player_one_killed,
         self.switch_to_player_two)[m[SCRIPT_STAGE]]()

    def script_four(self):
        m = self.m
        self.handle_swarm_movement()
        self.set_alien_presence_flags()
        (self.reset_for_turn_0550,
         self.clear_row_of_screen,
         self.player_two_init,
         self.clear_player_text,
         self.handle_spawn_player,
         self.handle_main_game_logic,
         self.handle_player_two_killed,
         self.switch_to_player_one)[m[SCRIPT_STAGE]]()

    def reset_for_turn_0550(self):                          # $0550
        m = self.m
        self.fill(ALIEN_SWARM_FLAGS, 0x80, 0)
        m[0x6000] = m[0x6001] = 0
        m[TIMING_VARIABLE] = 0
        self.fill(0x4200, 0x17, 0)
        self.fill(0x4218, 0x18, 0)
        self.fill(ENEMY_BULLETS, 0x46, 0)
        m[0x4226] = 1
        m[SCRIPT_STAGE] += 1
        m[TEMP_COUNTER_2] = 0x20
        self.ww(TEMP_CHAR_RAM_PTR, 0x5000)

    def clear_row_of_screen(self):                          # $0583
        m = self.m
        hl = self.fill(self.rw(TEMP_CHAR_RAM_PTR), 0x20, 0x10)
        self.ww(TEMP_CHAR_RAM_PTR, hl)
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[SCRIPT_STAGE] += 1
        self.set_colour_attributes(0x1D71)

    def set_colour_attributes(self, table):                 # $0598
        m = self.m
        for i in range(32):
            m[0x4021 + i * 2] = ROM[table + i]

    def player_one_init(self):                              # $05A5
        m = self.m
        de = self.unpack_alien_swarm(0x4180)
        m[CURRENT_PLAYER_STATE:CURRENT_PLAYER_STATE + 8] = m[de:de + 8]
        m[TIMING_VARIABLE] = 0
        m[0x4220] = 0
        m[PORT_HFLIP] = m[PORT_VFLIP] = 0
        m[DISPLAY_IS_COCKTAIL_P2] = 0
        m[SCRIPT_STAGE] += 1
        m[TEMP_COUNTER_2] = 0x96
        m[0x4245] = 0x40
        m[0x4246] = 0x06
        if not (m[IS_GAME_IN_PLAY] & 1):
            return
        if m[IS_TWO_PLAYER_GAME] & 1:
            self.queue_command(5, 3)
        else:
            self.queue_command(5, 0)
        self.queue_command(5, 2)
        self.queue_command(6, 2)
        self.queue_command(6, 4)
        self.queue_command(7, 3)
        self.queue_command(7, 0)

    def player_two_init(self):                              # $0795
        m = self.m
        de = self.unpack_alien_swarm(0x41A0)
        m[CURRENT_PLAYER_STATE:CURRENT_PLAYER_STATE + 8] = m[de:de + 8]
        m[TIMING_VARIABLE] = 0
        m[0x4220] = 0
        a = m[IS_COCKTAIL]
        if a:
            m[DISPLAY_IS_COCKTAIL_P2] = a
            m[PORT_HFLIP] = m[PORT_VFLIP] = a
        m[SCRIPT_STAGE] += 1
        m[TEMP_COUNTER_2] = 0x96
        m[0x4245] = 0x30
        m[0x4246] = 0x08
        if not (m[IS_GAME_IN_PLAY] & 1):
            return
        self.queue_command(5, 3)
        self.queue_command(6, 3)
        self.queue_command(6, 4)
        self.queue_command(7, 3)
        self.queue_command(7, 0)

    def clear_player_text(self):                            # $0605
        m = self.m
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[TEMP_COUNTER_2] = 0x14
        m[SCRIPT_STAGE] += 1
        self.queue_command(6, 0x82)

    def handle_spawn_player(self):                          # $0614
        m = self.m
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[TEMP_COUNTER_2] = 0x0A
        m[SCRIPT_STAGE] += 1
        m[HAS_PLAYER_SPAWNED] = 1
        m[IS_PLAYER_DYING] = 0
        m[PLAYER_Y] = 0x80
        for i in range(16):
            m[0x424A + i] = ROM[0x15E3 + i]
        m[0x4058] = 0
        m[0x405A] = 0
        self.queue_command(7, 3)
        self.queue_command(2, 0)

    def unpack_alien_swarm(self, de, rom=False):            # $0646
        m = self.m
        hl = ALIEN_SWARM_FLAGS
        for _ in range(16):
            v = ROM[de] if rom else m[de]
            for bit in range(8):
                m[hl] = 1 if v & (1 << bit) else 0
                hl += 1
            de += 1
        return de

    def pack_alien_swarm(self, de):                         # $0764
        m = self.m
        hl = ALIEN_SWARM_FLAGS
        for _ in range(16):
            a = 0
            for bit in range(8):
                if m[hl] & 1:
                    a |= 1 << bit
                hl += 1
            m[de] = a
            de += 1
        return de

    def handle_main_game_logic(self):                       # $0661
        m = self.m
        self.handle_player_move()
        self.handle_player_bullet()
        self.handle_enemy_bullets()
        self.handle_inflight_aliens()
        self.handle_inflight_alien_sprite_update()
        self.handle_player_shoot()
        self.handle_swarm_alien_to_player_bullet_collision()
        self.handle_player_to_enemy_bullet_collision()
        self.handle_inflight_alien_to_player_bullet_collision()
        self.handle_player_to_inflight_alien_collision()
        self.check_if_player_bullet_expired()
        self.handle_flagship_attack()
        self.handle_single_alien_attack()
        self.set_alien_attack_flank()
        self.handle_level_difficulty()
        self.handle_player_hit()
        self.handle_player_dying()
        self.legacy_16a6()
        self.check_if_alien_can_attack()
        self.update_attack_counters()
        self.check_if_flagship_can_attack()
        self.handle_calc_inflight_alien_shooting_distance()
        self.check_if_level_is_complete()
        self.handle_level_complete()
        self.handle_alien_aggressiveness()
        self.handle_shocked_swarm()
        self.handle_simulate_player_in_attract_mode()
        # $06B2 - can this stage end?
        if (m[HAS_PLAYER_BULLET_BEEN_FIRED] | m[HAS_PLAYER_SPAWNED] | m[IS_PLAYER_DYING]) & 1:
            return
        if not (m[0x4225] & 1):
            return
        a = 0
        for i in range(14):
            a |= m[ENEMY_BULLETS + i * 5]
        if a & 1:
            return
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[SCRIPT_STAGE] += 1

    def handle_player_one_killed(self):                     # $06D8
        m = self.m
        if m[PLAYER_LIVES]:
            # $0701
            if m[0x41B5] and m[IS_TWO_PLAYER_GAME]:
                m[SCRIPT_STAGE] += 1
                m[TEMP_COUNTER_2] = 0x50
                return
            self._respawn_0712()
            return
        if m[0x41B5] == 0 or m[IS_TWO_PLAYER_GAME] == 0:
            self.game_over()
            return
        m[SCRIPT_STAGE] += 1
        m[TEMP_COUNTER_2] = 0x82
        if not (m[IS_GAME_IN_PLAY] & 1):
            return
        self.queue_command(6, 2)
        self.queue_command(6, 0)

    def _respawn_0712(self):                                # $0712
        m = self.m
        if m[IS_GAME_IN_PLAY] & 1:
            m[SCRIPT_STAGE] = 4
        else:
            m[SCRIPT_STAGE] = 0x0E
        m[TEMP_COUNTER_2] = 0x50

    def game_over(self):                                    # $0722
        m = self.m
        if not (m[IS_GAME_IN_PLAY] & 1):
            m[SCRIPT_STAGE] += 1
            m[TEMP_COUNTER_2] = 0x50
            return
        m[SCRIPT_NUMBER] = 1
        m[IS_GAME_IN_PLAY] = 0
        m[SCRIPT_STAGE] = 0
        self.reset_sound()
        self.queue_command(6, 0)

    def switch_to_player_two(self):                         # $073D
        m = self.m
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[SCRIPT_STAGE] = 0
        m[0x4222] = 0
        m[0x422B] = 0
        de = self.pack_alien_swarm(0x4180)
        m[de:de + 8] = m[CURRENT_PLAYER_STATE:CURRENT_PLAYER_STATE + 8]
        m[CURRENT_PLAYER] = 1
        m[SCRIPT_NUMBER] = 4

    def handle_player_two_killed(self):                     # $07E8
        m = self.m
        if m[PLAYER_LIVES]:
            # $080C
            if m[0x4195] == 0:
                self._respawn_0712()
                return
            m[SCRIPT_STAGE] += 1
            m[TEMP_COUNTER_2] = 0x50
            return
        if m[0x4195] == 0:
            self.game_over()
            return
        m[SCRIPT_STAGE] += 1
        m[TEMP_COUNTER_2] = 0x82
        if not (m[IS_GAME_IN_PLAY] & 1):
            return
        self.queue_command(6, 3)
        self.queue_command(6, 0)

    def switch_to_player_one(self):                         # $0818
        m = self.m
        m[TEMP_COUNTER_2] = (m[TEMP_COUNTER_2] - 1) & 0xFF
        if m[TEMP_COUNTER_2]:
            return
        m[SCRIPT_STAGE] = 0
        m[CURRENT_PLAYER] = 0
        m[SCRIPT_NUMBER] = 3
        de = self.pack_alien_swarm(0x41A0)
        m[de:de + 8] = m[CURRENT_PLAYER_STATE:CURRENT_PLAYER_STATE + 8]

    # ==================================================================
    # Player
    # ==================================================================
    def handle_player_move(self):                           # $0837
        m = self.m
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            # $0877 SPAWN_PLAYER_OR_DIE
            if m[IS_PLAYER_DYING] & 1:
                self._set_ship_scroll((~m[PLAYER_Y] + 0x80) & 0xFF, 7)
            else:
                m[PLAYER_Y] = 0
                self._set_ship_scroll((~m[PLAYER_Y] + 0x80) & 0xFF, 6)
            return
        if not (m[IS_GAME_IN_PLAY] & 1):
            b = m[0x423F]
        elif m[DISPLAY_IS_COCKTAIL_P2] & 1:
            b = m[PORT_STATE_6800]
        else:
            b = m[PORT_STATE_6000]
        if b & 0x08 and m[PLAYER_Y] >= 0x17:
            m[PLAYER_Y] -= 1
        if b & 0x04 and m[PLAYER_Y] < 0xE9:
            m[PLAYER_Y] += 1
        self._set_ship_scroll((~m[PLAYER_Y] + 0x80) & 0xFF, 6)

    def _set_ship_scroll(self, a, c):                       # $086B
        m = self.m
        hl = 0x4054
        for _ in range(4):
            m[hl] = a
            m[hl + 1] = c
            hl += 2

    def handle_player_bullet(self):                         # $0898
        m = self.m
        self.position_player_bullet()
        l = m[PLAYER_BULLET_X]
        h = m[PLAYER_BULLET_Y]
        if m[DISPLAY_IS_COCKTAIL_P2] & 1:
            m[0x409F] = (l - 1) & 0xFF
        else:
            m[0x409F] = (~l - 4) & 0xFF
        m[0x409D] = ~h & 0xFF

    def position_player_bullet(self):                       # $08BC
        m = self.m
        if m[HAS_PLAYER_BULLET_BEEN_FIRED] & 1:
            x = (m[PLAYER_BULLET_X] - 4) & 0xFF
            m[PLAYER_BULLET_X] = x
            a = (x - 0x0E) & 0xFF
            if a < 4:                    # carry out of the second SUB 4
                m[IS_PLAYER_BULLET_DONE] = 1
            return
        m[PLAYER_BULLET_X] = 0xDC
        if m[HAS_PLAYER_SPAWNED] & 1:
            m[PLAYER_BULLET_Y] = m[PLAYER_Y]
        else:
            m[PLAYER_BULLET_Y] = 0

    def check_if_player_bullet_expired(self):               # $08E5
        m = self.m
        if m[IS_PLAYER_BULLET_DONE] & 1:
            m[IS_PLAYER_BULLET_DONE] = 0
            m[HAS_PLAYER_BULLET_BEEN_FIRED] = 0

    def handle_player_shoot(self):                          # $0A32
        m = self.m
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        if m[HAS_PLAYER_BULLET_BEEN_FIRED] & 1:
            return
        if not (m[IS_GAME_IN_PLAY] & 1):
            if (m[TIMING_VARIABLE] & 0x1F) == 0:
                m[HAS_PLAYER_BULLET_BEEN_FIRED] = 1
            return
        if m[DISPLAY_IS_COCKTAIL_P2] & 1:
            b = ~m[PREV_PORT_STATE_6800] & 0xFF
            a = m[PORT_STATE_6800]
        else:
            b = ~m[PREV_PORT_STATE_6000] & 0xFF
            a = m[PORT_STATE_6000]
        if a & b & 0x10:
            m[HAS_PLAYER_BULLET_BEEN_FIRED] = 1
            m[PLAY_PLAYER_SHOOT_SOUND] = 1

    # ==================================================================
    # Swarm
    # ==================================================================
    def handle_swarm_movement(self):                        # $090D
        m = self.m
        if m[HAS_PLAYER_BULLET_BEEN_FIRED] & 1:
            if ((m[PLAYER_BULLET_X] - 0x22) & 0xFF) < 0x50:
                a = (-(m[SWARM_SCROLL_VALUE] - m[PLAYER_BULLET_Y])) & 0xFF
                if ((a + 2) & 0x0F) < 3:
                    col = (a >> 4) & 0x0F
                    if m[0x41F0 + col] & 1:
                        # $0988: the swarm holds still
                        self.set_swarm_scroll_offset((-m[SWARM_SCROLL_VALUE]) & 0xFF)
                        return
        hl = self.rw(SWARM_SCROLL_VALUE)
        e = m[SWARM_SCROLL_MAX_EXTENTS]
        d = m[SWARM_SCROLL_MAX_EXTENTS + 1]
        h, l = hl >> 8, hl & 0xFF
        if m[SWARM_DIRECTION] == 0:
            if not (h & 0x80) and l >= e:
                m[SWARM_DIRECTION] = 1
                return
            if m[TIMING_VARIABLE] & 3:
                return
            hl = (hl + 1) & 0xFFFF
        else:
            if (h & 0x80) and l < d:
                m[SWARM_DIRECTION] = 0
                return
            if m[TIMING_VARIABLE] & 3:
                return
            hl = (hl - 1) & 0xFFFF
        self.ww(SWARM_SCROLL_VALUE, hl)
        self.set_swarm_scroll_offset((-(hl & 0xFF)) & 0xFF)

    def set_swarm_scroll_offset(self, a):                   # $0972
        m = self.m
        for i in range(9):
            m[0x4028 + i * 2] = a

    def set_alien_presence_flags(self):                     # $098E
        m = self.m
        m[0x41E8] = 0
        m[0x41E9] = 0
        de = 0x41EA
        base = 0x4123
        for row in range(6):
            a = 0
            for i in range(10):
                a |= m[base + row * 16 + i]
            m[de] = a
            de += 1
        m[0x41F0] = m[0x41F1] = m[0x41F2] = 0
        de = 0x41F3
        for col in range(10):
            a = 0
            for row in range(6):
                a |= m[base + col + row * 16]
            m[de] = a
            de += 1
        # $09CE scroll extents
        e = 0x22
        hl = 0x41FC
        for _ in range(10):
            if m[hl] & 1:
                break
            hl -= 1
            e = (e + 0x10) & 0xFF
        else:
            e = 0x22
        d = 0xE0
        hl = 0x41F3
        for _ in range(10):
            if m[hl] & 1:
                break
            hl += 1
            d = (d - 0x10) & 0xFF
        else:
            d = 0xE0
        m[SWARM_SCROLL_MAX_EXTENTS] = e
        m[SWARM_SCROLL_MAX_EXTENTS + 1] = d
        # $09FA
        a = m[0x41EA] | m[0x41EB] | m[0x41EC] | m[0x41ED]
        a ^= 1
        m[0x4221] = a
        a ^= 1
        a = (a | m[0x41EE] | m[0x41EF]) ^ 1
        m[0x4220] = a
        a = 0
        for i in range(7):
            a |= m[0x42D0 + i * 32]
        a ^= 1
        m[0x4226] = a
        a ^= 1
        for i in range(8):
            a |= m[0x42B1 + i * 32]
        m[0x4225] = a ^ 1

    # ==================================================================
    # Enemy bullets
    # ==================================================================
    def handle_enemy_bullets(self):                         # $0A74
        m = self.m
        ix = ENEMY_BULLETS
        if not (m[TIMING_VARIABLE] & 1):
            m[ix + 1] = (m[ix + 1] + 2) & 0xFF
            ix += 5
        iy = 0x4081
        cocktail = m[DISPLAY_IS_COCKTAIL_P2] & 1
        for b in range(7, 0, -1):
            alive = False
            if m[ix] & 1:
                a = (m[ix + 1] + 2) & 0xFF
                m[ix + 1] = a
                if a + 4 <= 0xFF:
                    hl = m[ix + 2] | (m[ix + 3] << 8)
                    e = m[ix + 4]
                    sign = e >> 7
                    e = (e << 1) & 0xFF
                    de = (0xFF00 if sign else 0) | e
                    hl = (hl + de) & 0xFFFF
                    m[ix + 2] = hl & 0xFF
                    m[ix + 3] = hl >> 8
                    if ((hl >> 8) + 0x10) & 0xFF >= 0x20:
                        alive = True
            if not alive:
                m[ix] = 0
                m[ix + 1] = 0
                m[ix + 3] = 0
            # $0AC6 position the bullet sprite
            if cocktail:
                m[iy + 2] = (m[ix + 1] - 4) & 0xFF
                c = ~m[ix + 3] & 0xFF
                if b >= 5:
                    c = (c - 1) & 0xFF
            else:
                m[iy + 2] = (~m[ix + 1] - 1) & 0xFF
                c = ~m[ix + 3] & 0xFF
                if b >= 5:
                    c = (c + 1) & 0xFF
            m[iy] = c
            ix += 5
            if ix + 1 < 0x10000:
                m[ix + 1] = (m[ix + 1] + 2) & 0xFF
            ix += 5
            iy += 4

    def handle_player_to_enemy_bullet_collision(self):      # $0B77
        m = self.m
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        py = m[PLAYER_Y]
        for i in range(14):
            ix = ENEMY_BULLETS + i * 5
            if not (m[ix] & 1):
                continue
            a = (m[ix + 1] + 0x1F) & 0xFF
            if a < 5:
                if ((py - m[ix + 3] + 2) & 0xFF) >= 5:
                    continue
            else:
                if a - 5 >= 9:
                    continue
                if ((py - m[ix + 3] + 5) & 0xFF) >= 0x0B:
                    continue
            m[ix] = 0
            m[IS_PLAYER_HIT] = 1

    # ==================================================================
    # Player bullet vs swarm
    # ==================================================================
    def handle_swarm_alien_to_player_bullet_collision(self):  # $0B0B
        m = self.m
        if not (m[HAS_PLAYER_BULLET_BEEN_FIRED] & 1):
            return
        a = m[PLAYER_BULLET_X]
        if a >= 0x68:
            return
        a -= 0x1E
        if a < 0:
            return
        b = 6
        while True:
            a -= 7
            if a < 0:
                return
            a -= 5
            if a < 0:
                break
            b -= 1
            if b == 0:
                return
        c = (-(m[SWARM_SCROLL_VALUE] - m[PLAYER_BULLET_Y])) & 0xFF
        if (((c & 0x0F) - 2) & 0xFF) >= 0x0B:
            return
        b += 1
        e = swap(((c & 0xF0) + b) & 0xFF)
        hl = ALIEN_SWARM_FLAGS + e
        if not (m[hl] & 1):
            return
        m[hl] = 0
        self.queue_command(1, e)
        m[IS_PLAYER_BULLET_DONE] = 1
        m[0x42B1] = 1
        m[0x42B2] = 0
        m[0x42B3] = m[PLAYER_BULLET_X]
        m[0x42B4] = m[PLAYER_BULLET_Y]
        if e < 0x50:
            self.queue_command(3, 0)
        else:
            self.queue_command(3, ((e & 0x70) >> 4) - 4)

    # ==================================================================
    # Sprites
    # ==================================================================
    def handle_inflight_alien_sprite_update(self):          # $0BBE
        m = self.m
        ix = INFLIGHT_ALIENS
        iy = 0x4060
        if m[DISPLAY_IS_COCKTAIL_P2] & 1:
            offs = (9, 9, 9, 8, 8, 8, 8, 8)
        else:
            offs = (7, 7, 7, 8, 8, 8, 8, 8)
        for i in range(8):
            self.set_sprite_state(ix, iy, offs[i])
            ix += 0x20
            iy += 4

    def set_sprite_state(self, ix, iy, c):                  # $0C20
        m = self.m
        if not (m[ix] & 1):
            # $0C98
            if m[ix + 1] & 1:
                m[iy + 2] = 7
                m[iy + 3] = (m[ix + 3] - 8) & 0xFF
                m[iy] = (~m[ix + 4] - c) & 0xFF
                m[iy + 1] = m[ix + 0x12]
            else:
                m[iy + 3] = 0xF8
                m[iy] = 0xF8
            return
        m[iy + 2] = m[ix + 0x16]
        m[iy + 3] = (m[ix + 3] - 8) & 0xFF
        m[iy] = (~m[ix + 4] - c) & 0xFF
        a = m[ix + 5]
        start = m[ix + 0x0F]
        while True:
            if not (a & 0x80):
                if (a - 6) & 0x80:
                    m[iy + 1] = ((((a + 0x11) & 0xFF) | 0xC0) + start) & 0xFF
                    m[iy + 3] = (m[iy + 3] + 1) & 0xFF
                    m[iy] = (m[iy] + 1) & 0xFF
                    return
                if not ((a - 0x0C) & 0x80):
                    a = (a - 0x18) & 0xFF
                    continue
                m[iy + 1] = ((((~a + 0x1E) & 0xFF) | 0x80) + start) & 0xFF
                m[iy] = (m[iy] + 1) & 0xFF
                return
            else:
                if not ((a - 0xFA) & 0x80):
                    m[iy + 1] = ((((~a + 0x12) & 0xFF) | 0x40) + start) & 0xFF
                    m[iy + 3] = (m[iy + 3] + 1) & 0xFF
                    return
                if (a - 0xF4) & 0x80:
                    a = (a + 0x18) & 0xFF
                    continue
                m[iy + 1] = (a + 0x1D + start) & 0xFF
                return

    # ==================================================================
    # Inflight aliens - "stages of life"
    # ==================================================================
    def handle_inflight_aliens(self):                       # $0CC3
        for i in range(8):
            self.handle_inflight_alien_stage_of_life(INFLIGHT_ALIENS + i * 0x20)

    def handle_inflight_alien_stage_of_life(self, ix):      # $0CD6
        m = self.m
        if m[ix + 1] & 1:
            st = m[ix + 2]
            if st == 0:
                self.inflight_alien_dying_setup(ix)
            elif st == 1:
                self.inflight_alien_dying_display_explosion(ix)
            elif st == 2:
                self.inflight_alien_dying_finally_buys_farm(ix)
            return
        if not (m[ix] & 1):
            return
        st = m[ix + 2]
        if st == 0:
            self.inflight_alien_packs_bags(ix)
        elif st == 1 or st == 11:
            self.inflight_alien_flies_in_arc(ix)
        elif st == 2:
            self.inflight_alien_ready_to_attack(ix)
        elif st == 3:
            self.inflight_alien_attacking_player(ix)
        elif st == 4:
            self.inflight_alien_near_bottom_of_screen(ix)
        elif st == 5:
            self.inflight_alien_reached_bottom_of_screen(ix)
        elif st == 6:
            self.inflight_alien_returning_to_swarm(ix)
        elif st == 7:
            self.inflight_alien_continuing_attack_run(ix)
        elif st == 8:
            self.inflight_alien_full_speed_charge(ix)
        elif st == 9:
            self.inflight_alien_attacking_player_aggressively(ix)
        elif st == 10:
            self.inflight_alien_loop_the_loop(ix)
        elif st == 12:
            self.inflight_alien_unknown_1091(ix)
        elif st == 13:
            self.convoy_charger_set_colour_pos_anim(ix)
        elif st == 14:
            self.convoy_charger_start_scroll(ix)
        elif st == 15:
            self.convoy_charger_do_scroll(ix)

    def inflight_alien_packs_bags(self, ix):                # $0D06
        m = self.m
        m[ix + 0x17] = 0
        m[ENABLE_ALIEN_ATTACK_SOUND] = 1
        self.set_inflight_alien_start_position(ix)
        e = m[ix + 7]
        self.queue_command(1, e)
        e = (e & 0x70) >> 3
        m[ix + 0x16] = ROM[0x1DD1 + e]
        m[ix + 0x18] = ROM[0x1DD1 + e + 1]
        if e == 0x0E:
            m[ix + 0x0F] = 0x18
            n = 0
            if m[ix + 0x20] & 1:
                n += 1
            if m[ix + 0x40] & 1:
                n += 1
            m[0x422A] = n
        else:
            m[ix + 0x0F] = 0
        m[ix + 0x10] = 3
        m[ix + 0x11] = 0x0C
        m[ix + 0x13] = 0
        m[ix + 2] += 1
        m[ix + 5] = 0xF4 if (m[ix + 6] & 1) else 0x0C

    def inflight_alien_flies_in_arc(self, ix):              # $0D71
        m = self.m
        l = m[ix + 0x13]
        m[ix + 3] = (m[ix + 3] + ROM[0x1E00 + l]) & 0xFF
        l = (l + 1) & 0xFF
        cw = m[ix + 6] & 1
        if cw:
            a = (m[ix + 4] - ROM[0x1E00 + l]) & 0xFF
        else:
            a = (m[ix + 4] + ROM[0x1E00 + l]) & 0xFF
        m[ix + 4] = a
        if ((a + 7) & 0xFF) < 0x0E:
            m[ix + 2] = 5
            return
        m[ix + 0x13] = (l + 1) & 0xFF
        m[ix + 0x10] = (m[ix + 0x10] - 1) & 0xFF
        if m[ix + 0x10]:
            return
        m[ix + 0x10] = 4
        m[ix + 5] = (m[ix + 5] + (1 if cw else -1)) & 0xFF
        m[ix + 0x11] = (m[ix + 0x11] - 1) & 0xFF
        if m[ix + 0x11]:
            return
        m[ix + 2] += 1

    def inflight_alien_ready_to_attack(self, ix):           # $0DD1
        m = self.m
        m[ix + 3] = (m[ix + 3] + 1) & 0xFF
        if (m[ix + 7] & 0x70) == 0x60 and (m[0x42D0] & 1):
            # red alien in a convoy: share the flagship's flight path
            self._store_flightpath(ix, m[0x42E9])
            return
        self.inflight_alien_define_flightpath(ix)

    def inflight_alien_define_flightpath(self, ix):         # $0DDD
        m = self.m
        py = m[PLAYER_Y]
        y = m[ix + 4]
        a = (y - py) & 0xFF
        if y < py:
            a = (a >> 1) | 0x80          # RRA with carry in = 1
            a = (a - 0x10) & 0xFF
            if a >= 0xD0:
                a = 0xD0
            if a < 0x90:
                a = 0x90
        else:
            a = a >> 1
            a = (a + 0x10) & 0xFF
            if a < 0x30:
                a = 0x30
            if a >= 0x70:
                a = 0x70
        self._store_flightpath(ix, a)

    def _store_flightpath(self, ix, a):                     # $0DF6
        m = self.m
        m[ix + 0x19] = a
        m[ix + 9] = (-((a - m[ix + 4]) & 0xFF)) & 0xFF
        m[ix + 0x1A] = 0
        m[ix + 0x1B] = 0
        m[ix + 0x1C] = 0
        m[ix + 2] += 1

    def _try_shoot(self, ix):                               # $0E54 / $0FE6
        m = self.m
        h = m[0x4214]
        l = m[0x4213]
        a = m[ix + 3]
        while True:
            if a == h:
                self.try_spawn_enemy_bullet(ix)
                return
            a = (a + 0x19) & 0xFF
            l = (l - 1) & 0xFF
            if l == 0:
                return

    def inflight_alien_attacking_player(self, ix):          # $0E2B
        m = self.m
        m[ix + 3] = (m[ix + 3] + 1) & 0xFF
        self.update_inflight_alien_yadd(ix)
        a = (m[ix + 9] + m[ix + 0x19]) & 0xFF
        m[ix + 4] = a
        if ((a + 7) & 0xFF) < 0x0E:
            m[ix + 2] += 2
            return
        if m[ix + 3] + 0x48 > 0xFF:
            m[ix + 2] += 1
            return
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        self.calculate_inflight_alien_lookat_anim_frame(ix)
        if m[0x422B] & 1:
            return
        self._try_shoot(ix)

    def inflight_alien_near_bottom_of_screen(self, ix):     # $0E6B
        m = self.m
        a = ((m[TIMING_VARIABLE] & 1) + 1 + m[ix + 3]) & 0xFF
        m[ix + 3] = a
        if ((a - 6) & 0xFF) < 3:
            m[ix + 2] += 1
            return
        self.update_inflight_alien_yadd(ix)
        a = m[ix + 0x19]
        s = a + m[ix + 9]
        if a & 0x80:
            if s > 0xFF:
                m[ix + 4] = s & 0xFF
                return
        else:
            if s <= 0xFF:
                m[ix + 4] = s
                return
        m[ix + 2] += 1

    def inflight_alien_reached_bottom_of_screen(self, ix):  # $0E99
        m = self.m
        m[ix + 3] = 8
        m[ix + 0x17] = (m[ix + 0x17] + 1) & 0xFF
        m[ix + 5] = 0
        if (m[ix + 7] & 0x70) == 0x70:
            # $0EDA flagship
            if m[0x422A] == 0:
                m[ix] = 0
                a = m[0x421E] + 1
                if a >= 3:
                    a = 2
                m[0x421E] = a
                return
            n = 0
            if m[ix + 0x20] & 1:
                n += 1
            if m[ix + 0x40] & 1:
                n += 1
            m[0x422A] = n
        self._reached_bottom_0ead(ix)

    def _reached_bottom_0ead(self, ix):                     # $0EAD
        m = self.m
        keep_attacking = False
        if m[HAS_PLAYER_SPAWNED] & 1:
            if m[0x4224] != 0 or m[0x4221] != 0:
                keep_attacking = True
        if keep_attacking:
            c = m[ix + 4] >> 1
            a = ((self.rand() & 0x1F) + c + 0x20) & 0xFF
            m[ix + 4] = a
            m[ix + 0x10] = 0x28
            m[ix + 2] += 1
        m[ix + 2] += 1

    def inflight_alien_returning_to_swarm(self, ix):        # $0F07
        m = self.m
        b = (m[ix + 3] + 1) & 0xFF
        self.set_inflight_alien_start_position(ix)
        a = m[ix + 3]
        m[ix + 3] = b
        a = (a - b) & 0xFF
        if a == 0:
            # $0F2B INFLIGHT_ALIEN_BACK_IN_SWARM
            m[ix] = 0
            idx = m[ix + 7]
            m[ALIEN_SWARM_FLAGS + idx] = 1
            self.queue_command(0, idx)
            return
        if a >= 0x19 or (a & 1):
            return
        if m[ix + 6] & 1:
            m[ix + 5] = (m[ix + 5] - 1) & 0xFF
        else:
            m[ix + 5] = (m[ix + 5] + 1) & 0xFF

    def inflight_alien_continuing_attack_run(self, ix):     # $0F3C
        m = self.m
        m[ix + 3] = (m[ix + 3] + 1) & 0xFF
        py = m[PLAYER_Y]
        y = m[ix + 4]
        v = (y - py) & 0xFF                 # SUB / NEG
        carry = 1 if py != y else 0
        e1 = ((v << 1) | carry) & 0xFF      # RLA
        c1 = v >> 7
        d = 0xFF if c1 else 0               # SBC A,A
        e2 = ((e1 << 1) | c1) & 0xFF        # RL E
        c2 = e1 >> 7
        d = ((d << 1) | c2) & 0xFF          # RL D
        hl = (y << 8) | m[ix + 9]
        hl = (hl - ((d << 8) | e2)) & 0xFFFF
        m[ix + 4] = hl >> 8
        m[ix + 9] = hl & 0xFF
        m[ix + 0x10] = (m[ix + 0x10] - 1) & 0xFF
        if m[ix + 0x10]:
            return
        m[ix + 2] += 1

    def inflight_alien_full_speed_charge(self, ix):         # $0F66
        m = self.m
        m[ix + 3] = (m[ix + 3] + 1) & 0xFF
        if ((m[ix + 3] - 0x60) & 0xFF) < 0x40 and ((m[ix + 4] - 0x60) & 0xFF) < 0x40:
            m[ix + 2] += 2
            m[ix + 0x10] = 3
            m[ix + 0x11] = 0x0C
            m[ix + 5] = 0
            m[ix + 0x13] = 0
            m[ix + 6] = 1 if m[PLAYER_Y] < m[ix + 4] else 0
            return
        self._veer_0f7b(ix)

    def _veer_0f7b(self, ix):                               # $0F7B
        m = self.m
        self.inflight_alien_define_flightpath(ix)
        m[ix + 0x18] = 3
        m[ix + 0x10] = 0x64

    def inflight_alien_attacking_player_aggressively(self, ix):  # $0FAF
        m = self.m
        m[ix + 3] = (m[ix + 3] + 1) & 0xFF
        self.update_inflight_alien_yadd(ix)
        s = m[ix + 0x17]
        if s > 4 or (s == 4 and (m[TIMING_VARIABLE] & 1)):
            # $100B hug the player
            if m[PLAYER_Y] >= m[ix + 9]:
                m[ix + 9] = (m[ix + 9] + 1) & 0xFF
            else:
                m[ix + 9] = (m[ix + 9] - 1) & 0xFF
        a = (m[ix + 9] + m[ix + 0x19]) & 0xFF
        m[ix + 4] = a
        if ((a + 7) & 0xFF) < 0x0E:
            m[ix + 2] = 5
            return
        if m[ix + 3] + 0x40 > 0xFF:
            m[ix + 2] = 4
            return
        m[ix + 0x10] = (m[ix + 0x10] - 1) & 0xFF
        if m[ix + 0x10] == 0:
            m[ix + 2] -= 1
            return
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        self.calculate_inflight_alien_lookat_anim_frame(ix)
        if m[0x422B] & 1:
            return
        self._try_shoot(ix)

    def inflight_alien_loop_the_loop(self, ix):             # $101F
        m = self.m
        l = m[ix + 0x13]
        m[ix + 3] = (m[ix + 3] - ROM[0x1E00 + l]) & 0xFF
        l = (l + 1) & 0xFF
        cw = m[ix + 6] & 1
        if cw:
            m[ix + 4] = (m[ix + 4] + ROM[0x1E00 + l]) & 0xFF
        else:
            m[ix + 4] = (m[ix + 4] - ROM[0x1E00 + l]) & 0xFF
        m[ix + 0x13] = (l + 1) & 0xFF
        m[ix + 0x10] = (m[ix + 0x10] - 1) & 0xFF
        if m[ix + 0x10]:
            return
        m[ix + 0x10] = 4
        m[ix + 5] = (m[ix + 5] + (1 if cw else -1)) & 0xFF
        m[ix + 0x11] = (m[ix + 0x11] - 1) & 0xFF
        if m[ix + 0x11]:
            return
        m[ix + 2] += 1
        m[ix + 0x10] = 3
        m[ix + 0x11] = 0x0C
        m[ix + 5] = 0xF4 if cw else 0x0C
        m[ix + 0x13] = 0

    def inflight_alien_unknown_1091(self, ix):              # $1091
        m = self.m
        m[ix + 3] = (m[ix + 3] + 1) & 0xFF
        m[ix + 2] = 8
        self._veer_0f7b(ix)

    def convoy_charger_set_colour_pos_anim(self, ix):       # $109B
        m = self.m
        b = (~m[ix + 7]) & 3
        a = b + 1
        m[ix + 0x16] = a
        m[ix + 3] = (0x8C + (a << 4)) & 0xFF
        m[ix + 0x10] = 0x18
        m[ix + 2] += 1
        m[ix + 0x0F] = 0x18 if b == 0 else 0

    def convoy_charger_start_scroll(self, ix):              # $10C2
        m = self.m
        m[ix + 4] = (m[ix + 4] + 1) & 0xFF
        m[ix + 0x10] = (m[ix + 0x10] - 1) & 0xFF
        if m[ix + 0x10]:
            return
        self.queue_command(6, (m[ix + 7] + 0x4B) & 0xFF)
        m[ix + 2] += 1

    def convoy_charger_do_scroll(self, ix):                 # $10D8
        m = self.m
        if ((m[ix + 4] - 0xC8) & 0xFF) < 5:
            return
        m[ix + 4] = (m[ix + 4] + 1) & 0xFF

    def inflight_alien_dying_setup(self, ix):               # $10F0
        m = self.m
        m[ix + 0x10] = 4
        m[ix + 0x11] = 4
        m[ix + 0x12] = 0x1C
        m[ix + 2] += 1
        m[ALIEN_DEATH_SOUND] = 0x17 if m[ix + 7] >= 0x70 else 0x07

    def inflight_alien_dying_display_explosion(self, ix):   # $1112
        m = self.m
        m[ix + 0x10] = (m[ix + 0x10] - 1) & 0xFF
        if m[ix + 0x10]:
            return
        m[ix + 0x10] = 4
        m[ix + 0x12] = (m[ix + 0x12] + 1) & 0xFF
        m[ix + 0x11] = (m[ix + 0x11] - 1) & 0xFF
        if m[ix + 0x11]:
            return
        if m[ix + 7] >= 0x70:
            # $112D DISPLAY_FLAGSHIP_POINTS_VALUE
            m[ix + 0x10] = 0x32
            m[ix + 0x12] = (m[0x422D] + 0x20) & 0xFF
            m[ix + 2] += 1
        else:
            m[ix + 1] = 0

    def inflight_alien_dying_finally_buys_farm(self, ix):   # $113D
        m = self.m
        m[ix + 0x10] = (m[ix + 0x10] - 1) & 0xFF
        if m[ix + 0x10]:
            return
        m[ix + 1] = 0

    def set_inflight_alien_start_position(self, ix):        # $1147
        m = self.m
        row = (m[ix + 7] & 0x70) >> 4
        m[ix + 3] = (0x7C - row * 12) & 0xFF
        col = m[ix + 7] & 0x0F
        m[ix + 4] = (m[SWARM_SCROLL_VALUE] + ((col << 4) & 0xFF) + 7) & 0xFF

    def update_inflight_alien_yadd(self, ix):               # $116B
        m = self.m
        b = (m[ix + 0x18] & 3) + 1
        h = m[ix + 0x19]
        l = m[ix + 0x1A]
        d = m[ix + 0x1B]
        e = m[ix + 0x1C]
        for _ in range(b):
            a = l
            c = h
            if a & 0x80:
                h = (h - 1) & 0xFF
            a = (a << 1) & 0xFF
            s = a + d
            d = s & 0xFF
            a = (h + (s >> 8)) & 0xFF
            if a == 0x80:
                a = c
            h = a
            c = l
            a = (-a) & 0xFF
            if a & 0x80:
                l = (l - 1) & 0xFF
            a = (a << 1) & 0xFF
            s = a + e
            e = s & 0xFF
            a = (l + (s >> 8)) & 0xFF
            if a == 0x80:
                a = c
            l = a
        m[ix + 0x19] = h
        m[ix + 0x1A] = l
        m[ix + 0x1B] = d
        m[ix + 0x1C] = e

    @staticmethod
    def calculate_tangent(a, d):                            # $0048
        c = 0
        for _ in range(8):
            if a < d:
                bit = 0
            else:
                a -= d
                bit = 1
            c = ((c << 1) | bit) & 0xFF
            d >>= 1
        return c

    def calculate_inflight_alien_lookat_anim_frame(self, ix):  # $11B0
        m = self.m
        d = (0xF0 - m[ix + 3]) & 0xFF
        py = m[PLAYER_Y]
        y = m[ix + 4]

        def f(a):
            a = self.calculate_tangent(a, d)
            if a & 0x80:
                a = 0x80
            return (a >> 5) & 7

        if py >= y:
            m[ix + 5] = f((py - y) & 0xFF)
        else:
            m[ix + 5] = (-f((y - py) & 0xFF)) & 0xFF

    def try_spawn_enemy_bullet(self, ix):                   # $11E0
        m = self.m
        for i in range(14):
            hl = ENEMY_BULLETS + i * 5
            if not (m[hl] & 1):
                break
        else:
            return
        # $11F0 SPAWN_ENEMY_BULLET
        m[hl] = 1
        x = m[ix + 3]
        m[hl + 1] = x
        d = (0xF0 - x) & 0xFF
        m[hl + 3] = m[ix + 4]
        py = m[PLAYER_Y]
        y = m[ix + 4]
        if py >= y:
            m[hl + 4] = self.compute_enemy_bullet_delta((py - y) & 0xFF, d)
        else:
            m[hl + 4] = (-self.compute_enemy_bullet_delta((y - py) & 0xFF, d)) & 0xFF

    def compute_enemy_bullet_delta(self, a, d):             # $1218
        c = self.calculate_tangent(a, d)
        a = ((self.rand() & 0x1F) + c + 6) & 0xFF
        return a if not (a & 0x80) else 0x7F

    # ==================================================================
    # Collisions
    # ==================================================================
    def handle_inflight_alien_to_player_bullet_collision(self):  # $1227
        m = self.m
        if not (m[HAS_PLAYER_BULLET_BEEN_FIRED] & 1):
            return
        for i in range(1, 8):
            ix = INFLIGHT_ALIENS + i * 0x20
            if not (m[ix] & 1):
                continue
            if ((m[ix + 3] - m[PLAYER_BULLET_X] + 2) & 0xFF) >= 6:
                continue
            if ((m[ix + 4] - m[PLAYER_BULLET_Y] + 5) & 0xFF) >= 0x0C:
                continue
            m[IS_PLAYER_BULLET_DONE] = 1
            self.kill_inflight_alien(ix)

    def kill_inflight_alien(self, ix):                      # $125E
        m = self.m
        m[ix] = 0
        m[ix + 1] = 1
        m[ix + 2] = 0
        e = 4
        a = m[ix + 7]
        for _ in range(3):
            if a < 0x50:
                self.queue_command(3, e)
                return
            e += 1
            a = (a - 0x10) & 0xFF
        # a flagship was shot down
        m[0x422B] = 1
        m[0x422C] = 0xF0
        a = m[0x422A]
        if a == 2 and not (m[ix + 0x20] & 1) and not (m[ix + 0x40] & 1):
            a = 3
        m[0x422D] = a
        self.queue_command(3, a + e)

    def handle_player_to_inflight_alien_collision(self):    # $129E
        m = self.m
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        for i in range(1, 8):
            ix = INFLIGHT_ALIENS + i * 0x20
            if not (m[ix] & 1):
                continue
            a = (m[ix + 3] + 0x21) & 0xFF
            py = m[PLAYER_Y]
            if a < 5:
                if ((py - m[ix + 4] + 7) & 0xFF) >= 0x0F:
                    continue
            else:
                if a - 5 >= 0x0C:
                    continue
                if ((py - m[ix + 4] + 0x0A) & 0xFF) >= 0x15:
                    continue
            m[IS_PLAYER_HIT] = 1
            self.kill_inflight_alien(ix)

    def handle_player_hit(self):                            # $12ED
        m = self.m
        if not (m[IS_PLAYER_HIT] & 1):
            return
        m[IS_PLAYER_HIT] = 0
        m[HAS_PLAYER_SPAWNED] = 0
        m[IS_PLAYER_DYING] = 1
        m[0x4205] = 0x0A
        m[0x4206] = 0x04
        self.queue_command(2, 5)
        if m[0x421A]:
            m[0x421A] -= 1
        m[PLAYER_LIVES] = (m[PLAYER_LIVES] - 1) & 0xFF
        if m[PLAYER_LIVES] >= 6:
            m[PLAYER_LIVES] = 5
        if not (m[IS_GAME_IN_PLAY] & 1):
            return
        m[0x6803] = 1

    def handle_player_dying(self):                          # $1327
        m = self.m
        if not (m[IS_PLAYER_DYING] & 1):
            return
        m[0x4205] = (m[0x4205] - 1) & 0xFF
        if m[0x4205]:
            return
        m[0x4205] = 0x0A
        self.queue_command(2, m[0x4206])
        m[0x4206] = (m[0x4206] - 1) & 0xFF
        if m[0x4206]:
            return
        m[IS_PLAYER_DYING] = 0
        m[0x6803] = 0

    # ==================================================================
    # Attack scheduling
    # ==================================================================
    def handle_single_alien_attack(self):                   # $1344
        m = self.m
        if not (m[0x4228] & 1):
            return
        m[0x4228] = 0
        if m[0x4220] & 1:
            return
        a = (m[0x421B] + m[0x421A]) >> 1
        if a >= 4:
            a = 3
        b = a + 1
        hl = 0x4391
        for _ in range(b):
            if (m[hl] | m[hl - 1]) == 0:
                ix = hl - 1
                break
            hl -= 0x20
        else:
            return
        a = m[0x4215]
        m[ix + 6] = a
        if a == 0:
            # $137B scan columns from the left flank (CPDR)
            hl, bc = 0x41FC, 10
            while True:
                match = m[hl] == 1
                hl -= 1
                bc -= 1
                if match or bc == 0:
                    break
            if not match or bc == 0:
                return
            e = 0x3F
            l = (hl + 1) & 0xFF
        else:
            # $13AB scan from the right flank (CPIR)
            hl, bc = 0x41F3, 10
            while True:
                match = m[hl] == 1
                hl += 1
                bc -= 1
                if match or bc == 0:
                    break
            if not match or bc == 0:
                return
            e = 0x41
            l = (hl - 1) & 0xFF
        # $138A TRY_FIND_ALIEN_TO_ATTACK
        c = bc
        if not (m[0x41EF] & 1):
            d = 5
            l = (l & 0x0F) + 0x60
            e = (e + 0x10) & 0xFF
        else:
            d = 4
            l = (l & 0x0F) + 0x50
        while True:
            for _ in range(d):
                if m[0x4100 | l] & 1:
                    # $13CE WAKEUP_INFLIGHT_ALIEN
                    m[0x4100 | l] = 0
                    m[ix + 7] = l
                    m[ix] = 1
                    m[ix + 2] = 0
                    self.queue_command(1, l)
                    return
                l = (l - 0x10) & 0xFF
            l = (l + e) & 0xFF
            c = (c - 1) & 0xFF
            if c == 0:
                return

    def set_alien_attack_flank(self):                       # $13E1
        m = self.m
        l = m[SWARM_SCROLL_VALUE]
        h = m[SWARM_SCROLL_VALUE + 1]
        e = m[SWARM_SCROLL_MAX_EXTENTS]
        d = m[SWARM_SCROLL_MAX_EXTENTS + 1]
        if h & 0x80:
            if ((l - d) & 0xFF) < 0x1C:
                m[0x4215] = 0
                return
        else:
            if ((e - l) & 0xFF) < 0x1C:
                m[0x4215] = 1
                return
        m[0x4215] = self.rand() & 1

    def handle_flagship_attack(self):                       # $140C
        m = self.m
        if m[0x4220] & 1:
            return
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        if not (m[0x4229] & 1):
            return
        m[0x4229] = 0
        if (m[0x42D0] | m[0x42D1]) & 1:
            return
        c = m[0x4215]
        if c & 1:
            hl = 0x4176
            for _ in range(4):
                if m[hl] & 1:
                    self._init_flagship_attack(hl, c, -0x11, +1)
                    return
                hl += 1
            l = 0x65
            for _ in range(4):
                if m[0x4100 | l] & 1:
                    self._try_init_inflight_alien(0x4100 | l, c)
                    return
                l += 1
        else:
            hl = 0x4179
            for _ in range(4):
                if m[hl] & 1:
                    self._init_flagship_attack(hl, c, -0x0F, -1)
                    return
                hl -= 1
            l = 0x6A
            for _ in range(4):
                if m[0x4100 | l] & 1:
                    self._try_init_inflight_alien(0x4100 | l, c)
                    return
                l -= 1

    def _try_init_inflight_alien(self, hl, c):              # $1446
        m = self.m
        ix = 0x4390
        for _ in range(4):
            if (m[ix] | m[ix + 1]) == 0:
                self._init_inflight_alien(hl, ix, c)
                return
            ix -= 0x20

    def _init_inflight_alien(self, hl, ix, c):              # $145C
        m = self.m
        m[hl] = 0
        m[ix] = 1
        m[ix + 2] = 0
        m[ix + 6] = c
        m[ix + 7] = hl & 0xFF
        self.queue_command(1, hl & 0xFF)

    def _init_flagship_attack(self, hl, c, offset, step):   # $1472 / $14D7
        m = self.m
        ix = 0x42D0
        self._init_inflight_alien(hl, ix, c)
        l = (hl + offset) & 0xFF
        iy = 0x42F0
        b, c = 3, 2
        while b:
            if m[0x4100 | l] & 1:
                # $148E
                if not (m[iy] & 1) and not (m[iy + 1] & 1):
                    m[0x4100 | l] = 0
                    m[iy] = 1
                    m[iy + 2] = 0
                    m[iy + 6] = m[ix + 6]
                    m[iy + 7] = l
                    self.queue_command(1, l)
                iy += 0x20
                c -= 1
                if c == 0:
                    b = 1
            l = (l + step) & 0xFF
            b -= 1

    def handle_level_difficulty(self):                      # $14F3
        m = self.m
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        if m[0x422B] & 1:
            return
        m[0x4218] = (m[0x4218] - 1) & 0xFF
        if m[0x4218]:
            return
        m[0x4218] = 0x3C
        m[0x4219] = (m[0x4219] - 1) & 0xFF
        if m[0x4219]:
            return
        m[0x4219] = 0x14
        a = m[0x421A]
        if a == 7:
            return
        m[0x421A] = 7 if a > 7 else a + 1

    def check_if_alien_can_attack(self):                    # $1515
        m = self.m
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        if m[0x4220] & 1:
            return
        if m[0x422B] & 1:
            return
        h = m[0x421B]
        a = h if h >= 2 else 0
        b = ((a + m[0x421A]) & 0x0F) + 1
        m[0x424A] = (m[0x424A] - 1) & 0xFF
        if m[0x424A]:
            m[0x4228] = 0
            return
        c = 0
        m[0x424A] = ROM[0x15E3]
        for i in range(1, b + 1):
            m[0x424A + i] = (m[0x424A + i] - 1) & 0xFF
            if m[0x424A + i] == 0:
                m[0x424A + i] = ROM[0x15E3 + i]
                c += 1
        if c:
            m[0x4228] = 1

    def update_attack_counters(self):                       # $1555
        m = self.m
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        if not (m[0x41EF] & 1):
            return
        if m[0x422B] & 1:
            return
        if not (m[IS_GAME_IN_PLAY] & 1):
            # $15A7
            m[0x4245] = (m[0x4245] - 1) & 0xFF
            if m[0x4245]:
                return
            m[0x4245] = 0x3C
            m[0x4246] = (m[0x4246] - 1) & 0xFF
            if m[0x4246]:
                return
            m[0x4246] = 5
            m[0x422F] = 0x5A
            m[0x424A] = 0x2D
            m[0x422E] = 1
            return
        m[0x4245] = (m[0x4245] - 1) & 0xFF
        if m[0x4245]:
            return
        m[0x4245] = 0x3C
        if m[0x4221] & 1:
            a = 2
        else:
            m[0x4246] = (m[0x4246] - 1) & 0xFF
            if m[0x4246]:
                return
            m[0x4246] = 1
            c = (m[0x4177] + m[0x4178]) & 3
            s = (m[0x421B] + m[0x421A]) & 0xFF
            if s == 0:
                return
            a = rrc(rrc(s)) & 3
            a = ((~a & 0xFF) + 0x0A - c) & 0xFF
            m[0x4246] = a
        a = rlc(rlc(a))
        m[0x422F] = a
        m[0x424A] = rlc(a)
        m[0x422E] = 1

    def check_if_flagship_can_attack(self):                 # $15C3
        m = self.m
        if not (m[0x422E] & 1):
            return
        m[0x422F] = (m[0x422F] - 1) & 0xFF
        if m[0x422F]:
            return
        m[0x422E] = 0
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        if not (m[0x41EF] & 1):
            return
        m[0x4229] = 1

    def handle_calc_inflight_alien_shooting_distance(self):  # $15F4
        m = self.m
        if m[0x421B] == 0:
            e, d = 1, 0x84
        else:
            e, d = 2, 0x9D
        hl = 0x41E8
        for _ in range(4):
            if m[hl] & 1:
                break
            hl += 1
            if m[hl] & 1:
                break
            hl += 1
            e += 1
        m[0x4213] = e & 0xFF
        m[0x4214] = d

    def check_if_level_is_complete(self):                   # $1621
        m = self.m
        if not (m[0x4220] & 1):
            return
        if not (m[0x4225] & 1):
            return
        if m[0x4222] & 1:
            return
        m[0x4222] = 1
        m[0x4223] = 0

    def handle_level_complete(self):                        # $1637
        m = self.m
        if not (m[0x4222] & 1):
            return
        m[0x4223] = (m[0x4223] - 1) & 0xFF
        if m[0x4223]:
            return
        m[0x4222] = 0
        self.unpack_alien_swarm(0x051B, rom=True)
        m[0x421A] = 0
        m[TIMING_VARIABLE] = 0
        self.ww(SWARM_SCROLL_VALUE, 1)
        l = m[0x421B]
        if l > 7:
            l = 7
        elif l < 7:
            l += 1
        m[0x421B] = l
        m[0x421C] = (m[0x421C] + 1) & 0xFF
        self.queue_command(7, 0)
        a = m[0x421E]
        if a == 0:
            return
        m[0x4177] = 1
        a -= 1
        m[0x421E] = a
        if a == 0:
            return
        m[0x4178] = 1
        m[0x421E] = 0

    def handle_shocked_swarm(self):                         # $1688
        m = self.m
        if not (m[0x422B] & 1):
            return
        if m[0x4224] == 0 and m[0x4221] == 0 and not (m[0x4226] & 1):
            return
        m[0x422C] = (m[0x422C] - 1) & 0xFF
        if m[0x422C]:
            return
        m[0x422B] = 0

    def legacy_16a6(self):                                  # $16A6
        m = self.m
        if m[IS_GAME_OVER] & 1:
            return
        a = m[ALIEN_DEATH_SOUND]
        if a == 0:
            return
        m[0x6804] = rrc(rrc(a))
        m[ALIEN_DEATH_SOUND] = a - 1

    def handle_alien_aggressiveness(self):                  # $16B8
        m = self.m
        if m[IS_GAME_OVER] & 1:
            return
        n = 0
        for row in range(6):
            for i in range(10):
                n += m[0x4123 + row * 16 + i]
        a = (n + 1) & 0xFF
        port = 0x6800
        for _ in range(3):
            a = (a - 1) & 0xFF
            if a == 0:
                while port < 0x6803:
                    m[port] = 0
                    port += 1
                break
            m[port] = 1
            port += 1
        m[0x4224] = 1 if a < 2 else 0

    # ==================================================================
    # Attract mode "AI" ($198E)
    # ==================================================================
    def handle_simulate_player_in_attract_mode(self):
        m = self.m
        if ((m[TIMING_VARIABLE] + 9) & 0x1F):
            return
        if not (m[IS_GAME_OVER] & 1):
            return
        if not (m[HAS_PLAYER_SPAWNED] & 1):
            return
        b = 0
        for i in range(1, 8):
            ix = INFLIGHT_ALIENS + i * 0x20
            b = self._dodge(ix, m[ix + 3], m[ix + 4], m[ix + 0x1A], b)
        for i in range(7):
            ix = ENEMY_BULLETS + i * 5
            b = self._dodge(ix, m[ix + 1], m[ix + 3], m[ix + 4], b)
        a = (m[SWARM_SCROLL_VALUE] + 0x80 - m[PLAYER_Y]) & 0xFF
        sa = a - 256 if a & 0x80 else a
        sa >>= 5
        a = (sa + b) & 0xFF
        sa = a - 256 if a & 0x80 else a
        c = (sa >> 1) & 0xFF
        r = self.rand()
        a = 0xFF if r & 0x80 else 0
        if a == 0:
            a = 1
        a = (a + c + 1) & 0xFF
        if a & 0x80:
            m[0x423F] = 0x08
        elif a >= 2:
            m[0x423F] = 0x04
        else:
            m[0x423F] = 0

    def _dodge(self, ix, h, l, c, b):                       # $1A12
        m = self.m
        if not (m[ix] & 1):
            return b
        a = h - 0x80
        if a < 0:
            return b
        e = 0
        a -= 0x34
        if a >= 0:
            e = 1
            a -= 0x34
            if a >= 0:
                return b
        a = (m[PLAYER_Y] - l - 0x40) & 0xFF
        if a >= 0x80:
            return b
        a &= 0x60
        e |= swap((c & 0x80) | a)
        return (b + ROM[0x1A45 + e]) & 0xFF

    # ==================================================================
    # Sound control code ($16F5..$18BF) - writes the sound latches
    # ==================================================================
    def handle_sound(self):                                 # $16F5
        m = self.m
        m[SOUND_VOL] = 0
        m[PITCH_SOUND_FX_BASE_FREQ] = 0xFF
        self.handle_game_start_melody()
        self.handle_alien_attack_sound()
        self.handle_alien_death_sound()
        self.handle_complex_sounds()
        self.handle_extra_life_sound()
        self.handle_coin_insert_sound()
        self.handle_player_shooting_sound()
        m[0x6806] = m[SOUND_VOL] & 1
        m[0x6807] = (m[SOUND_VOL] >> 1) & 1
        m[PORT_PITCH] = m[PITCH_SOUND_FX_BASE_FREQ]

    def handle_player_shooting_sound(self):                 # $1723
        m = self.m
        if m[PLAY_PLAYER_SHOOT_SOUND] == 1:
            m[PLAY_PLAYER_SHOOT_SOUND] = 0
            m[0x41CE] = 8
            return
        a = m[0x41CE]
        if a == 0:
            m[0x6805] = 0
            return
        m[0x41CE] = a - 1
        m[0x6805] = (m[IS_GAME_OVER] ^ 1) & 1

    def handle_game_start_melody(self):                     # $1747
        m = self.m
        if m[PLAY_GAME_START_MELODY] != 1:
            return
        m[PLAY_GAME_START_MELODY] = 0
        m[0x41D2] = 1
        m[0x41D6] = 1
        self.ww(0x41D3, 0x1E68)

    def handle_complex_sounds(self):                        # $175D
        for flag in (0x41D2, 0x41CF, IS_COMPLEX_SOUND_PLAYING):
            self._complex_sound(flag)

    def _complex_sound(self, flag):                         # $176C
        m = self.m
        if m[flag] == 0:
            return
        m[SOUND_VOL] = 2
        m[PITCH_SOUND_FX_BASE_FREQ] = m[0x41D5]
        a = (m[0x41D6] - 1) & 0xFF
        if a == 0:
            p = self.rw(0x41D3)
            note = ROM[p]
            if note == 0xE0:
                m[flag] = 0
                return
            self.ww(0x41D3, p + 1)
            m[0x41D5] = ROM[0x17A9 + (note & 0x1F)]
            a = ROM[0x17C8 + (note >> 5)]
        m[0x41D6] = a

    def handle_alien_attack_sound(self):                    # $17D0
        m = self.m
        if not (m[IS_GAME_IN_PLAY] & 1):
            return
        if m[ENABLE_ALIEN_ATTACK_SOUND] == 1:
            m[ENABLE_ALIEN_ATTACK_SOUND] = 0
            m[0x41C3] = 0x02
            m[0x41C4] = 0xA0
            return
        if m[0x4226] & 1:
            return
        if not (m[TIMING_VARIABLE] & 1):
            a = m[0x41C4]
            if a < 0x60:
                m[0x41C3] = (m[0x41C3] + 1) & 0xFF
            if a:
                m[0x41C4] = a - 1
        a = m[0x41C3] & 3
        if a == 0:
            pitch = 0x60
        elif a & 1:
            s = m[0x41C4] + 0x60
            pitch = ((s >> 1) | ((s >> 8) << 7)) & 0xFF
        else:
            pitch = m[0x41C4]
        m[PITCH_SOUND_FX_BASE_FREQ] = pitch

    def handle_alien_death_sound(self):                     # $1819
        m = self.m
        if not (m[IS_GAME_IN_PLAY] & 1):
            return
        a = m[ALIEN_DEATH_SOUND]
        if a == 6:
            if m[IS_COMPLEX_SOUND_PLAYING] & 1:
                return
            m[0x41CF] = 1
            m[0x41D6] = 1
            self.ww(0x41D3, 0x1EBD)
        elif a == 0x16:
            m[0x41CF] = 0
            m[IS_COMPLEX_SOUND_PLAYING] = 1
            m[0x41D6] = 1
            self.ww(0x41D3, 0x1EDF)

    def handle_extra_life_sound(self):                      # $184F
        m = self.m
        if m[PLAY_EXTRA_LIFE_SOUND] & 1:
            m[PLAY_EXTRA_LIFE_SOUND] = 0
            m[0x41C8] = 0x80
            return
        h = m[0x41C8]
        if h == 0:
            return
        a = h - 1
        m[0x41C8] = a
        m[PITCH_SOUND_FX_BASE_FREQ] = 0x80 if (a & 4) else 0xFF
        m[SOUND_VOL] = 1

    def handle_coin_insert_sound(self):                     # $1876
        m = self.m
        if m[PLAY_PLAYER_CREDIT_SOUND] == 1:
            m[PLAY_PLAYER_CREDIT_SOUND] = 0
            m[0x41CA] = 0x20
            m[0x41CB] = 0
            return
        if m[0x41CA] == 0:
            return
        m[0x41CA] -= 1
        m[0x41CB] = (m[0x41CB] + 4) & 0xFF
        m[PITCH_SOUND_FX_BASE_FREQ] = m[0x41CB]
        m[SOUND_VOL] = 0

    def handle_swarm_sound(self):                           # $1898
        m = self.m
        if m[RESET_SWARM_SOUND_TEMPO]:
            m[RESET_SWARM_SOUND_TEMPO] = 0
            a = 0x0F
        else:
            if m[TIMING_VARIABLE] != 0xFF:
                return
            a = m[0x421F]
            if a == 0:
                return
            a -= 1
        m[0x421F] = a
        for i in range(4):
            m[0x6004 + i] = a & 1
            a = rrc(a)

    # ==================================================================
    # Coins
    # ==================================================================
    def check_if_coin_inserted(self):                       # $18EF
        m = self.m
        if m[DIP_SWITCH_1_2_STATE] == 3:
            m[COIN_COUNT] = 0
            m[NUM_CREDITS] = 9
            return
        a = (~(m[PORT_STATE_6000] | m[PREV_PORT_STATE_6000]) & 0xFF)
        a &= m[PREV_PREV_PORT_STATE_6000] & m[PREV_PREV_PREV_STATE_6000]
        if a & 0x80:
            if m[NUM_CREDITS] < 0x63:
                m[NUM_CREDITS] += 1
                m[PLAY_PLAYER_CREDIT_SOUND] = 1
                self.queue_command(7, 1)
            return
        a &= 3
        if a == 0:
            return
        m[UNPROCESSED_COINS] = (m[UNPROCESSED_COINS] + 1) & 0xFF
        if a == 3:
            m[UNPROCESSED_COINS] = (m[UNPROCESSED_COINS] + 1) & 0xFF

    def handle_unprocessed_coins(self):                     # $1931
        m = self.m
        a = m[COIN_CONTROL]
        if a:
            m[0x6003] = rrc(rrc(rrc(a)))
            m[COIN_CONTROL] = a - 1
            return
        if m[UNPROCESSED_COINS] == 0:
            return
        m[UNPROCESSED_COINS] -= 1
        m[COIN_CONTROL] = 0x0F
        a = m[DIP_SWITCH_1_2_STATE]
        if a == 3:
            return
        if a == 1:
            if m[COIN_COUNT] & 1:
                m[COIN_COUNT] = 0
                self._add_credit()
            else:
                m[COIN_COUNT] = 1
            return
        if a == 2:
            self._add_credit()
        self._add_credit()

    def _add_credit(self):                                  # $194F
        m = self.m
        a = m[NUM_CREDITS]
        if a == 0x63:
            return
        if a > 0x63:
            m[NUM_CREDITS] = 0x63
            return
        m[NUM_CREDITS] = a + 1
        m[PLAY_PLAYER_CREDIT_SOUND] = 1
        self.queue_command(7, 1)

    def handle_coin_lockout(self):                          # $197C
        self.m[0x6002] = 1 if self.m[NUM_CREDITS] < 9 else 0

    # ==================================================================
    # Column text scroller ($18C0) - SCORE ADVANCE TABLE lines
    # ==================================================================
    def handle_text_scroll(self):
        m = self.m
        if not (m[IS_COLUMN_SCROLLING] & 1):
            return
        hl = self.rw(0x40B1)
        if (m[hl] & 7) == 0:
            p = self.rw(0x40B3)
            ch = ROM[p]
            if ch != 0x3F:
                self.ww(0x40B3, p + 1)
                dst = self.rw(0x40B5)
                m[dst] = (ch - 0x30) & 0xFF
                self.ww(0x40B5, (dst - 32) & 0xFFFF)
        m[hl] = (m[hl] - 1) & 0xFF
        if m[hl]:
            return
        m[IS_COLUMN_SCROLLING] = 0

    # ==================================================================
    # Command queue handlers (run from the main loop)
    # ==================================================================
    def get_alien_char_ram_addr(self, a):                   # $20E1
        col = a & 0x0F
        row = (a >> 4) & 7
        odd = row & 1
        off = (~((row >> 1) + row + odd)) & 0x0F
        return 0x5000 + col * 0x40 + off, odd

    def draw_alien_command(self, a):                        # $2055
        m = self.m
        hl, odd = self.get_alien_char_ram_addr(a)
        # $2104 CHOOSE_ANIMATION_FRAME_FOR_ALIEN_REJOINING_SWARM
        if a >= 0x70:
            b = 0x80
        else:
            col = a & 0x0F
            c = 0xFF if (m[TIMING_VARIABLE] & 0x0F) < col else 0
            b = self.calculate_alien_animation_frame_index(col, c)
        self.draw_alien(b, hl, odd)

    def calculate_alien_animation_frame_index(self, b, c):  # $211D
        if b >= 0x70:
            return 0x80
        return (swap(self.m[TIMING_VARIABLE]) + b + c) & 3

    def draw_alien(self, b, hl, odd):                       # $2131
        if not odd:
            a = ROM[0x2157 + b]
            self.plot_two_characters_in_same_column(a, hl)
        else:
            a = 0xA4 if (b & 0x80) else ROM[0x215B + b]
            self.plot_characters_2_by_2_ascending(a, hl)

    def delete_alien_command(self, a):                      # $205E
        hl, odd = self.get_alien_char_ram_addr(a)
        if odd:
            self.plot_characters_2_by_2_ascending(0x2C, hl)
        else:
            self.plot_two_characters_in_same_column(0x2C, hl)

    def handle_swarm_animation(self):                       # $2067
        m = self.m
        t = m[TIMING_VARIABLE]
        col = t & 0x0F
        if col == 0:
            self.handle_1up_2up_blinking(t)
            return
        if m[0x4238] & 1:
            return
        idx = 0x20 + col
        for _ in range(6):
            if m[0x4100 + idx] & 1:
                hl, odd = self.get_alien_char_ram_addr(idx)
                b = self.calculate_alien_animation_frame_index(idx, 0)
                self.draw_alien(b, hl, odd)
            else:
                self.delete_alien_command(idx)
            idx += 0x10

    def handle_1up_2up_blinking(self, b):                   # $209C
        m = self.m
        if m[IS_GAME_IN_PLAY]:
            m[CAN_BLINK_1UP_2UP] = m[IS_GAME_IN_PLAY]
        elif m[CAN_BLINK_1UP_2UP] == 0:
            return
        a = m[CURRENT_PLAYER]
        hl = 0x5340 if a == 0 else 0x50E0
        if b & 0x10:
            m[hl] = m[hl - 32] = m[hl - 64] = 0x10
            if m[IS_TWO_PLAYER_GAME] == 0:
                return
            a ^= 1
            hl = 0x5340 if a == 0 else 0x50E0
        m[hl] = a + 1
        m[hl - 32] = 0x25
        m[hl - 64] = 0x20
        if b & 0x10:
            return
        if m[IS_GAME_IN_PLAY]:
            return
        m[CAN_BLINK_1UP_2UP] = 0

    def display_player_command(self, a):                    # $215F
        if a == 0:
            self.erase_player_ship()
            self.plot_characters_2_by_2_ascending(0x60, 0x51FC)
        elif a == 1:
            self.erase_player_ship()
        else:
            a = ((~(((a - 2) << 4) & 0xFF)) & 0x30) + 0xC0
            a = self.plot_characters_2_by_2_ascending(a, 0x51DA)[1]
            a = self.plot_characters_2_by_2_ascending(a, 0x51DC)[1]
            a = self.plot_characters_2_by_2_ascending(a, 0x521A)[1]
            self.plot_characters_2_by_2_ascending(a, 0x521C)

    def erase_player_ship(self):                            # $2187
        m = self.m
        hl = 0x51DA
        for _ in range(4):
            for _ in range(4):
                m[hl] = 0x40
                hl += 1
            hl += 0x1C

    def lea_current_player_score(self):                     # $2290
        return PLAYER_TWO_SCORE if self.m[CURRENT_PLAYER] else PLAYER_ONE_SCORE

    def update_player_score_command(self, c):               # $21A6
        m = self.m
        if m[IS_GAME_OVER] & 1:          # RST $08 ASSERT_NOT_GAME_OVER
            return
        de = self.lea_current_player_score()
        t = 0x22D0 + c * 3
        carry = 0
        for i in range(3):
            v = bcd_to_int(m[de + i]) + bcd_to_int(ROM[t + i]) + carry
            carry = 1 if v >= 100 else 0
            m[de + i] = int_to_bcd(v % 100)
        thousands = ((m[de + 2] << 4) | (m[de + 1] >> 4)) & 0xFF
        if thousands >= m[BONUS_GALIXIP_FOR]:
            self.award_extra_life()
        self.decide_to_display_score(m[CURRENT_PLAYER], de + 2)
        # high score?
        p, h = de + 2, HI_SCORE + 2
        for _ in range(3):
            if m[p] < m[h]:
                return
            if m[p] != m[h]:
                # $21E9 UPDATE_HIGH_SCORE
                m[HI_SCORE:HI_SCORE + 3] = m[de:de + 3]
                self.plot_score_characters(de + 2, 0x5241)
                return
            p -= 1
            h -= 1

    def award_extra_life(self):                             # $229C
        m = self.m
        hl = 0x40AD + m[CURRENT_PLAYER]
        if m[hl] & 1:
            return
        m[hl] = 1
        m[PLAY_EXTRA_LIFE_SOUND] = 1
        m[PLAYER_LIVES] = (m[PLAYER_LIVES] + 1) & 0xFF
        self.display_player_ships_remaining(m[PLAYER_LIVES])

    def display_player_ships_remaining(self, b):            # $22B3
        m = self.m
        hl = 0x539E
        c = 5
        if m[HAS_PLAYER_SPAWNED]:
            b = (b - 1) & 0xFF
            draw = b != 0
        else:
            draw = True
        if draw:
            if b == 0:
                b = 256
            while b:
                hl = self.plot_characters_2_by_2_descending(0x66, hl)
                c = (c - 1) & 0xFF
                b -= 1
        while True:
            c = (c - 1) & 0xFF
            if c & 0x80:
                return
            hl = self.plot_characters_2_by_2_descending(0x2E, hl)

    def reset_score_command(self, a):                       # $21FE
        m = self.m
        if a >= 3:
            while True:
                a -= 1
                self.reset_score_command(a)
                if a == 0:
                    return
        if a == 0:
            hl, de = PLAYER_ONE_SCORE, 0x40AD
        elif a == 1:
            hl, de = PLAYER_TWO_SCORE, 0x40AE
        else:
            hl = de = HI_SCORE
        m[hl] = m[hl + 1] = m[hl + 2] = 0
        m[de] = 0
        self.display_score_command(a)

    def display_score_command(self, a):                     # $2231
        m = self.m
        if a >= 3:
            while True:
                a -= 1
                self.display_score_command(a)
                if a == 0:
                    return
        if a == 0:
            self.decide_to_display_score(0, 0x40A4)
        elif a == 1:
            if m[IS_TWO_PLAYER_GAME] == 0:
                return
            self.decide_to_display_score(m[IS_TWO_PLAYER_GAME], 0x40A7)
        else:
            self.plot_score_characters(0x40AA, 0x5241)

    def decide_to_display_score(self, a, de):               # $2256
        self.plot_score_characters(de, 0x5381 if a == 0 else 0x5121)

    def plot_score_characters(self, hl, ix):                # $2261
        m = self.m
        c = 4
        for _ in range(3):
            for digit in (m[hl] >> 4, m[hl] & 0x0F):
                if digit:
                    c = 0
                    a = digit
                elif c:
                    a = 0x80
                    c -= 1
                else:
                    a = 0
                m[ix] = (a + 0x90) & 0xFF
                ix -= 32
            hl -= 1

    def print_text(self, a):                                # $22F1
        m = self.m
        p = ROM.word(0x235C + ((a * 2) & 0x3F))
        hl = ROM.word(p)
        de = p + 2
        if a & 0x80:
            # erase
            while ROM[de] != 0x3F:
                m[hl] = 0x40
                de += 1
                hl = (hl - 32) & 0xFFFF
        elif a & 0x40:
            # $2323 set up a column scroll
            self.ww(0x40B5, hl)
            self.ww(0x40B3, de)
            col = hl & 0x1F
            attr = 0x4020 + col * 2
            self.ww(0x40B1, attr)
            c = ((((hl >> 8) & 3) << 6) | ((hl & 0xFF) >> 2)) & 0xF8
            p = 0x5000 + col
            for _ in range(32):
                m[p] = 0x10
                p += 32
            m[attr] = c
            m[IS_COLUMN_SCROLLING] = 1
        else:
            while True:
                ch = (ROM[de] - 0x30) & 0xFF
                if ch == 0x0F:
                    return
                m[hl] = ch
                de += 1
                hl = (hl - 32) & 0xFFFF

    def display_bottom_of_screen(self, a):                  # $24B7
        m = self.m
        if a == 0:
            self.display_level_flags()
        elif a == 1:
            self.display_available_credit()
        elif a == 2:
            self.display_bonus_galixip_for()
        else:
            if m[IS_GAME_OVER] & 1:
                return
            self.display_player_ships_remaining(m[PLAYER_LIVES])

    def display_bonus_galixip_for(self):                    # $24C8
        m = self.m
        v = m[BONUS_GALIXIP_FOR]
        if v == 0xFF:
            return
        self.print_text(6)
        m[0x5138] = v & 0x0F
        a = v & 0xF0
        if a == 0:
            a = 1
        m[0x5158] = swap(a)

    def display_available_credit(self):                     # $24EB
        m = self.m
        if m[IS_GAME_IN_PLAY] & 1:
            return
        if (m[PORT_STATE_6800] & 0xC0) == 0xC0:
            self.print_text(0x10)
            return
        self.print_text(5)
        a = min(m[NUM_CREDITS], 0x63)
        a = self.convert_a_to_bcd(a)[0]
        if a & 0xF0:
            m[0x529F] = a >> 4
        m[0x527F] = a & 0x0F

    def convert_a_to_bcd(self, v):                          # $2569
        lo = v & 0x0F
        c = lo + 6 if lo > 9 else lo
        return int_to_bcd(v % 100), c

    def display_level_flags(self):                          # $2520
        m = self.m
        if m[IS_GAME_OVER] & 1:
            return
        if m[0x4220]:
            m[RESET_SWARM_SOUND_TEMPO] = 1
        a = m[0x421C] + 1
        if a >= 0x30:
            a = 0x30
        a, c = self.convert_a_to_bcd(a)
        hl = 0x507E
        if a & 0xF0:
            c = 0x10
            for _ in range(a >> 4):
                hl = self.plot_characters_2_by_2_ascending(0x68, hl)[0]
                c = (c - 2) & 0xFF
        for _ in range(a & 0x0F):
            hl = self.plot_two_chars_on_same_row(0x6C, hl, 0x1F)
            c = (c - 1) & 0xFF
        while True:
            c = (c - 1) & 0xFF
            if c & 0x80:
                return
            hl = self.plot_two_chars_on_same_row(0x2C, hl, 0x1F)

    # ------------------------------------------------------------------
    # character plotting primitives
    # ------------------------------------------------------------------
    def plot_two_chars_on_same_row(self, a, hl, de):        # $25A0
        m = self.m
        m[hl & 0xFFFF] = a & 0xFF
        m[(hl + 1) & 0xFFFF] = (a + 1) & 0xFF
        return (hl + 1 + de) & 0xFFFF

    def plot_characters_2_by_2_ascending(self, a, hl):      # $2585
        hl = self.plot_two_chars_on_same_row(a, hl, 0x1F)
        hl = self.plot_two_chars_on_same_row(a + 2, hl, 0x1F)
        return hl, (a + 4) & 0xFF

    def plot_characters_2_by_2_descending(self, a, hl):     # $2593
        hl = self.plot_two_chars_on_same_row(a, hl, -33)
        hl = self.plot_two_chars_on_same_row(a - 2, hl, -33)
        return hl

    def plot_two_characters_in_same_column(self, a, hl):    # $25A9
        self.m[hl] = a & 0xFF
        self.m[hl + 0x20] = (a + 2) & 0xFF
