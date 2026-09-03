# Synthetic AP frequent-offline fault tree

This fixture contains no company data. It is used only to validate automatic
knowledge routing.

## Fault tree

When an AP repeatedly goes offline and recovers, evaluate these branches:

1. Exclude physical link and power loss with explicit GW/AP evidence.
2. Check whether the AP UDM process stopped listening or restarted.
3. Correlate AP heartbeat-send failures with the GW heartbeat timeout.

Each branch must end as supported, excluded, or insufficient evidence.
