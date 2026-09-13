import logging
import time

try:
    import mido
except Exception:  # cloud hosts have no CoreMIDI / rtmidi
    mido = None

logger = logging.getLogger(__name__)

MIDI_MAP = {
    "deck_a_play": 10,
    "deck_b_play": 11,
    "crossfader": 12,
    "deck_a_eq_low": 20,
    "deck_a_eq_mid": 21,
    "deck_a_eq_hi": 22,
    "deck_b_eq_low": 23,
    "deck_b_eq_mid": 24,
    "deck_b_eq_hi": 25,
}


class DJController:
    def __init__(self, port_name="AI_DJ_Virtual_Port"):
        self.port_name = port_name
        self.outport = None
        self.error = None
        self._initialize_midi()

    @property
    def connected(self) -> bool:
        return self.outport is not None

    def _initialize_midi(self):
        if mido is None:
            self.error = "MIDI disabled (no mido/rtmidi on this host)"
            logger.warning(self.error)
            return
        try:
            self.outport = mido.open_output(self.port_name, virtual=True)
            logger.info("Virtual MIDI port '%s' created (RtMidi/CoreMIDI).", self.port_name)
        except Exception as e:
            self.error = str(e)
            logger.error("Error creating virtual MIDI port: %s", e)

    def send_cc(self, channel, control, value):
        if not self.outport:
            logger.warning("MIDI outport not initialized. Cannot send command.")
            return False
        msg = mido.Message(
            "control_change",
            channel=channel,
            control=control,
            value=max(0, min(127, int(value))),
        )
        self.outport.send(msg)
        logger.info("Sent MIDI: %s", msg)
        return True

    def deck_play_pause(self, deck=1):
        control = MIDI_MAP["deck_a_play"] if deck == 1 else MIDI_MAP["deck_b_play"]
        self.send_cc(channel=0, control=control, value=127)
        time.sleep(0.08)
        self.send_cc(channel=0, control=control, value=0)
        return True

    def crossfader(self, value):
        midi_val = max(0, min(127, int(float(value) * 127)))
        return self.send_cc(channel=0, control=MIDI_MAP["crossfader"], value=midi_val)

    def set_eq(self, deck=1, band="low", value=0.5):
        cc_map = {
            1: {"low": 20, "mid": 21, "hi": 22},
            2: {"low": 23, "mid": 24, "hi": 25},
        }
        control = cc_map[deck][band]
        midi_val = max(0, min(127, int(float(value) * 127)))
        return self.send_cc(channel=0, control=control, value=midi_val)
