# OrbitOps Inventory Procedures (IP-7, revision 4)

## 1. Purpose
This document defines how OrbitOps counts, reconciles and reorders cargo held at Earth Orbit Dock, Lunar Gateway and Mars Relay Depot.

## 2. Cycle counts
- Every storage bay is cycle-counted **every 72 hours**. Counts are performed by the duty quartermaster with a second verifier for any SKU valued above 10,000 credits.
- A count is recorded as **RECONCILED** only when the physical count matches the system quantity within 1 unit. Larger variances open an inventory incident (section 5).
- Hazardous cargo (hydrazine cells, oxidizer canisters) is counted **daily** and the count is countersigned by the safety officer.

## 3. Reorder rules
- A SKU is **below reorder point** when on-hand quantity is less than its reorder point. The flight controller must flag these items in any stock report.
- Reorder quantity = (reorder point x 2) - on-hand, rounded up to the supplier's minimum lot size.
- Navigation gyro assemblies (NAV-GYRO-X) are long-lead items (45 days). Reorder them when on-hand drops below **6 units**, not at zero.

## 4. Receiving
- Incoming cargo is quarantined in the receiving bay until quality inspection (see Quality Control Manual QC-3) returns PASSED.
- Only PASSED cargo may be moved into an active storage bay and added to on-hand quantity.

## 5. Inventory incidents
- A variance greater than 1 unit, or any missing hazardous item, is an inventory incident. Open it within 1 hour, notify the dock supervisor, and freeze the bay until resolved.
- Incident reports use the template INC-INV-01 and are retained for 5 years.
