# Synthetic GW/AP heartbeat protocol rule

This fixture contains no company data. It is used only to validate automatic
knowledge routing.

## Protocol diagnosis rule

For a managed AP, compare the AP Advertise heartbeat sequence with the GW
timeout state machine. A timeout alone is not proof of a transport fault. Check
the AP-side UDM listener and send result before assigning the root cause.
