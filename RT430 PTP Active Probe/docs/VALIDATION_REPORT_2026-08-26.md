# PTP functional validation — 26 August 2026

The Python active probe was tested with an RT430 clock and a Vebko relay test set. Missing peer-delay responses were isolated to the path through an intermediate switch. After removing it, the final capture recorded 92 complete Pdelay request/response/follow-up sequences, and the Vebko software indicated Detected and Time is Valid.

These results support functional communication and synchronization-status troubleshooting; they do not establish hardware-timestamp timing accuracy or certification. Raw packet captures and bench screenshots are retained privately and are not distributed in this public package.

Contribution: requirements definition, AI-assisted development guidance, practical testing and iterative refinement by Enzo Fernandes.
