# rekordbox MIDI Learn — `AI_DJ_Virtual_Port`

DJ Bot Botty creates a virtual MIDI port named **AI_DJ_Virtual_Port**. Enable it in rekordbox MIDI / controller settings, then MIDI-learn the controls below.

Deck **1** uses MIDI channel **1** (zero-based channel 0). Deck **2** uses MIDI channel **2** (zero-based channel 1). Continuous controls are CC 0–127 (0.0–1.0 in the API). Momentary taps send 127 then 0.

| Control | Deck | MIDI ch (1-based) | CC | Type |
|---------|------|-------------------|----|------|
| Play / Pause | 1 | 1 | 10 | tap |
| Cue | 1 | 1 | 11 | tap |
| Sync | 1 | 1 | 13 | tap |
| Load | 1 | 1 | 14 | tap |
| Volume | 1 | 1 | 15 | continuous |
| Filter | 1 | 1 | 16 | continuous |
| EQ Low | 1 | 1 | 20 | continuous |
| EQ Mid | 1 | 1 | 21 | continuous |
| EQ Hi | 1 | 1 | 22 | continuous |
| Browse Up | 1 | 1 | 30 | tap |
| Browse Down | 1 | 1 | 31 | tap |
| Play / Pause | 2 | 2 | 10 | tap |
| Cue | 2 | 2 | 11 | tap |
| Sync | 2 | 2 | 13 | tap |
| Load | 2 | 2 | 14 | tap |
| Volume | 2 | 2 | 15 | continuous |
| Filter | 2 | 2 | 16 | continuous |
| EQ Low | 2 | 2 | 20 | continuous |
| EQ Mid | 2 | 2 | 21 | continuous |
| EQ Hi | 2 | 2 | 22 | continuous |
| Browse Up | 2 | 2 | 30 | tap |
| Browse Down | 2 | 2 | 31 | tap |
| Crossfader | — | 1 | 12 | continuous |

## Dev without rekordbox

Set `DJ_BACKEND=sim` to use `FakeMidiOut` + `DeckSimulator` (works on Windows/Linux; no mido required).

## Blocked on Charles

Import this mapping via rekordbox MIDI Learn on `AI_DJ_Virtual_Port` on the Mac booth machine.
