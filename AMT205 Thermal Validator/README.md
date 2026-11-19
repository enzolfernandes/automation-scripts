# AMT205 Thermal Validator

Windows application for monitoring and documenting thermal tests on the Vebko AMT205 V8 test set. It passively observes communication between AMPro and the test set; it does not transmit packets or change the injection.

## Quick start

1. Confirm that Wireshark/Npcap is installed.
2. Double-click `Start AMT205 Thermal Validator.bat`.
3. Open **Settings** and enter the output location, identification and AMPro values.
4. Return to **Monitor** and choose **PCAP File** or **Live Monitor**.
5. In Live Monitor, press **Connect / F5** and wait for temperatures. Press **Start Recording / F6**, keep injection OFF while the fan-specific initial condition is measured, then follow **Next Step** through injection and recovery. Max Power uses the approved historical comparison at **50 Hz**; Fan Mode Normal is available as a comparative internal route. With automatic finalization enabled, the validator detects injection OFF, records recovery to the applicable target and generates the report without another operator action. Use **Disconnect / F5** only when live monitoring is no longer needed.

To compare successive tests, open **Session Compilation**, select the folder that represents the test series and click **Generate Compilation**. The application orders the discovered sessions chronologically, assigns cycle numbers and creates an Excel workbook plus a PNG graph in a timestamped `compilations` folder. The workbook includes intervals between cycles, initial and maximum temperatures, recovery results, a common-time thermal comparison and the underlying injection readings. Original session files are only read and are not changed.

The adopted 50%, 75% and 100% historical times were themselves recorded at 50 Hz with **Fan Mode (Max Power)**. Their default Max Power factor is therefore 1×; applying 3× again would duplicate the fan condition. The factor remains editable under Acceptance Criteria for a future, separately approved reference set. A compilation shows both the quantitative comparison recalculated from the reference and the result originally stored by the validator version that created each session.

In Live Monitor, the application asks Windows which adapter is routed to the AMT205 at `192.168.1.41`, selects it automatically and marks it as **AMT route**. Confirm this label before connecting; if Windows cannot determine the route, select the adapter connected to the test set manually.

Live Monitor can connect after a test has already started. The current temperatures will appear after the next AMT205 status packet—normally within approximately 1.5 seconds while idle and much faster during injection. If the initial condition was not captured, monitoring still works, but the formal result remains `INCONCLUSIVE`.

If no AMT205 status packet is received for five seconds, the Monitor changes to `DISCONNECTED`, clears the last temperatures so stale values are not mistaken for live readings, and records the connection loss in Events. When packets return, values resume automatically and a connection-restored event is recorded. Both events are preserved in the Excel report.

If the primary Excel-generation component is unavailable, the validator automatically uses its direct XlsxWriter fallback. Only if every generator fails is completion reported as **Session saved without Excel report**; the original capture, decoded readings, events and metadata always remain preserved.

Stopping a recording does not disconnect Live Monitor. Temperatures continue to update while the Excel report is generated, and another recording can be started without reconnecting. Clicking Disconnect immediately clears the live values.

**Next Step** is driven by decoded packet states rather than a fixed wall-clock script. Before injection, the hottest of the eight V/I sensors must remain around the target for the selected Fan Mode: 25 °C in Max Power or 33 °C in Normal mode. The default band is ±1 °C for 120 seconds, with range no greater than 1 °C and absolute trend no greater than 0.20 °C/min. The window restarts after a Fan Mode mismatch, an out-of-band reading, excessive range or a packet gap longer than five seconds. These values are editable under **Settings → Acceptance Criteria**.

The initial condition only confirms that the test starts from a documented, quiet condition with injection OFF. After injection, recovery uses the same fan-specific target and 120 s hold; recording continues until recovery is confirmed or the configurable maximum duration is reached. Initial stabilization and recovery are operational measurements and do not alter the injection acceptance result. The separate injection-stability rule remains available for profiles that explicitly use it.

Each session creates one operator-facing Excel report. Technical CSV/JSON files and the original live capture are preserved under `evidence/` for audit and reprocessing.

## Application layout

- **Monitor** contains the source, Connect/Disconnect, Start/Stop Recording, operating state, live temperatures, events and report shortcuts.
- **Settings** contains session identification, output location, AMPro values and acceptance criteria. Every field includes a plain-language explanation.
- **Session Compilation** treats the selected folder as a successive test series and combines its cycles into one Excel workbook and one comparison graph without reopening their packet captures.
- **Guide** provides the operating workflow, channel legend, result meanings and safety limitations inside the application.

The live temperature table separates the mapped sensors into color-coded groups:

- **Voltage**: V1/V2 Board sensors;
- **Current**: I1/I2 Board sensors;
- **Internal**: Main Board sensors.

The Monitor shows the hottest Voltage and Current sensors independently. The formal acceptance control remains the highest temperature among all eight Voltage and Current sensors. CPU Temperature remains excluded because its protocol field has not been mapped.

## AMPro terminology

The interface records the same labels used by AMPro:

- `Smart Delay (Turn On/Off Fan)`;
- `Min.` and `Max.`;
- `Max. Value`;
- `Earth — Enable`;
- `Fan Mode (Max Power)`;
- `Power Control`.

## Profiles

- Current — 1×128 A / 60 A / Power Control 25%;
- Current — 1×128 A / 60 A / Power Control 50%;
- Current — 1×128 A / 60 A / Power Control 75%;
- Current — 1×128 A / 60 A / Power Control 100%;
- Voltage — 6×300 V;
- Free Profile.

Free Profile supports Current, Voltage, Mixed or Other signals and one of four behaviors: Monitor only, Historical time reference, Stable temperature limits, or Reach stability without Thermal Error. Use **Monitor only** when there is no approved acceptance criterion.

Session and AMPro fields are saved automatically in `config/user.json` and restored when the application is reopened. The saved values include source mode, output location, profile, serial number, ambient temperature, repetition, Smart Delay, Max. Value, Earth, Fan Mode (Max Power) and feedback comparison. These user-interface preferences are not copied into immutable session evidence.

## Recognized protocol data

- ten confirmed internal temperature sensors;
- automatic compatibility with legacy 1067-byte and updated 1112-byte AMT205 status responses;
- Fan Mode decoding from legacy 110-byte and updated 124/129-byte AMPro requests;
- hottest V/I sensor used as the control temperature;
- hottest mapped internal sensor with CPU excluded;
- Max Power and Normal Fan Mode selection and decoding; Normal sessions are comparative while approved automatic references remain Max Power-only;
- active/inactive injection;
- internal State Time when available;
- Thermal Error when internal code 6 is latched;
- progress status 14 is recorded as the operator-confirmed **IA1 Other** electrical-output endpoint; it can close a historical-time run after its effective reference time, but it does not prove thermal stabilization. Internal code 6 remains the separate Thermal Error condition;
- an unlatched or otherwise ineligible status 14 remains an unidentified error termination;
- known voltage/current vectors and State durations;
- approved internal 120 s thermal-stability window (range ≤1.0 °C and absolute slope ≤0.20 °C/min), separately reported from the electrical result;
- automatic, configurable initial stabilization and post-injection recovery with fan-specific targets recorded in the report;
- conservative automatic evaluation based only on approved profile rules.

## Known limitations

- `CPU Temperature` is not mapped and appears as **Not mapped**.
- `Max. Value`, `Cooling`, `Smart Delay (Turn On/Off Fan)` and `Earth — Enable` are recorded from operator input; their protocol fields are not confirmed.
- A six-channel vector cannot always be classified as Current or Voltage without the preceding setup message.
- Automatic evaluation does not replace signatures and technical review.
- State Time and progress statuses 5, 6 and 14 are decoded in both the legacy and updated response layouts; updated status 14 is recorded as the operator-confirmed `IA1 Other` electrical endpoint for current-reference profiles.
- Thermal acceptance is calculated independently from the electrical endpoint using the approved internal 120 s window and configured temperature limits.
- The 25 °C Max Power and 33 °C Normal targets, ±1 °C tolerance and 120 s hold are internal operational conditions, not manufacturer limits or injection-acceptance criteria.

## Report structure

- `Summary`;
- `Results`;
- `Readings`;
- `Events`;
- `Charts`;
- `Configuration`;
- `Traceability`.

## Versioning

The application uses semantic versioning. The protocol map has an independent version because a field can be refined without changing the approved thermal-test criteria.

Run automated tests with:

```powershell
py -3 -m unittest discover -s tests -v
```
