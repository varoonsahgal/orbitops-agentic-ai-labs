# OrbitOps Quality Control Manual (QC-3, revision 6)

## 1. Scope
Every shipment arriving at an OrbitOps dock is inspected before it is released to inventory. This manual governs inspection intake, criteria, dispositions and records.

## 2. Inspection intake
- An inspection record is opened automatically when a shipment is created in logistics. Its status is **PENDING** until an inspector completes it.
- Inspections are performed by certified QC droids (Theta and Sigma series) or a human inspector holding QC level 2.

## 3. Acceptance criteria
- Propulsion parts (ion thruster coils): resistance within 2% of specification, no visible arc damage.
- Cryogenic seals: no micro-fractures under 40x magnification; a single fracture fails the batch.
- Fuel cells: pressure test at 110% of rated pressure for 30 minutes with no measurable loss.
- Navigation gyros: calibration drift at or below 0.05 degrees over a 10-minute spin test.

## 4. Dispositions
- **PASSED**: release to inventory and close the record.
- **FAILED**: quarantine the entire batch, notify the supplier within 24 hours, and open a supplier incident. Failed cargo is never moved into an active storage bay.
- **PENDING**: cargo stays in the receiving bay. Pending inspections older than 48 hours are escalated to the QC lead.

## 5. Records
- Inspection findings must name the inspector, the shipment id, the criteria applied and the result. Records are retained for 7 years.
- A summary of open inspections is reported to the flight controller at the start of every shift.
