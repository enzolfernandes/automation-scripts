# AMT205 V8 Protocol Map

Version: `AMT205-V8-2026-09-01.7`

This document records what application version 0.7.1 interprets. The monitor is passive: it does not create or retransmit any of these packets.

## Known transport

- test set: `192.168.1.41:1001`;
- computer: dynamic TCP port;
- legacy main response: 1067 bytes beginning with `04 00 00 00`;
- legacy response with progress: 1067 bytes plus a 34-byte trailer;
- updated main response: 1112 bytes beginning with `04 00 00 00`, with the value `1112` (`58 04`) at offsets 6–7;
- updated response with progress: 1112-byte base plus a 26-byte trailer; total/advertised size 1138 (`72 04`) at offsets 6–7;
- updated periodic computer requests have been observed with 124 and 129 bytes rather than the legacy 110 bytes;
- the updated response is 45 bytes longer overall. The confirmed temperature, execution and error fields are shifted by 10 bytes; the remaining additions must not be assumed to share one global offset.

## Confirmed temperature fields

Values are `int32 little-endian`, divided by 10.

| Legacy offset | Updated offset | Field | Use |
|---:|---:|---|---|
| 197 | 207 | V1 Board 1 | voltage control |
| 201 | 211 | V2 Board 1 | voltage control |
| 205 | 215 | V1 Board 2 | voltage control |
| 209 | 219 | V2 Board 2 | voltage control |
| 213 | 223 | I1 Board 1 | current control |
| 217 | 227 | I2 Board 1 | current control |
| 221 | 231 | I1 Board 2 | current control |
| 225 | 235 | I2 Board 2 | current control |
| 233 | 243 | Main Board 1 | informative |
| 241 | 251 | Main Board 2 | informative |

`CPU Temperature` is not mapped. Hottest V/I uses the eight control fields; hottest mapped internal uses only Main Board 1 and Main Board 2 and still excludes CPU.

## Confirmed states

- Fan Mode: legacy 110-byte request at offset 10 or updated 124/129-byte request at offset 18; `0x00` Normal and `0x3F` Max Power. The 129-byte Max Power format was confirmed in session `AMT205_20260901_135122`; the five added bytes do not move the Fan Mode field;
- execution: ten `int32` values at legacy offsets 329..365 or updated offsets 339..375 equal to `1,000,000,000`;
- latched Thermal Error: legacy header offset 4 = 1 and offset 8 = 6; updated offsets are 14 and 18;
- legacy progress: two equal `uint64` values at trailer offsets 4 and 12 in microseconds, and status at byte 29;
- updated progress: the same duplicated elapsed values at offsets 4 and 12 in the 26-byte trailer, and status at byte 25;
- progress status 5: start; 6: running; 14: injection terminated with an error whose type is not contained in this field;
- in the synchronized updated capture, status 14 at `214.308 s` coincided with the operator-confirmed AMPro error `IA1 Other`;
- status 14 is therefore classified as the operator-confirmed `IA1 Other` electrical-output endpoint for current-reference profiles, unless the same reading explicitly contains internal code 6 (Thermal Error). It is not, by itself, evidence of thermal stabilization;
- observed Clear Error command: `00 08 00 00` from the computer to the test set.

## Injection vectors

- 95- or 167-byte messages, type 16;
- State index at offset 9;
- 12-byte groups beginning at offset 13;
- amplitude: `int32` in micro-units;
- phase: `int32` in microdegrees;
- frequency: `uint32` in microhertz;
- in a 167-byte packet: the first six groups are Voltage and the next six are Current.

Six-channel messages preserve amplitude, phase and frequency, but Current/Voltage classification depends on the preceding setup and remains unknown when it cannot be proven from the isolated packet.

## Pending fields

- CPU Temperature;
- Max. Value;
- Cooling and Smart Delay (Turn On/Off Fan) flags;
- Earth — Enable;
- internal electrical feedback for automatic setpoint comparison;
- network Serial Number, if available.
- general decoding of all possible AMPro textual error labels; `IA1 Other` is currently associated with confirmed status 14 evidence, not extracted as text from the packet.

These fields require a new protocol-map revision after a synchronized capture and validation against the AMPro screen.
