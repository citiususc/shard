# Data Constraints

- Ontology: ontology.ttl
- Author: SHARD example fixture
- Date: 2026-07-15
- Description: Example rules for generating SHACL shapes over an asset maintenance ontology.

---

## Rule

- Number: BR-001
- Title: Asset identifier

### Data constraint

Every Asset must have exactly one asset identifier. The identifier must be a non-empty string.

---

## Rule

- Number: BR-002
- Title: Asset lifecycle status

### Data constraint

Every Asset must have exactly one lifecycle status. The lifecycle status must reference a LifecycleStatus value.

---

## Rule

- Number: BR-003
- Title: Asset location

### Data constraint

Every Asset must be located in exactly one Site.

---

## Rule

- Number: BR-004
- Title: Machine sensor coverage

### Data constraint

Every Machine must have at least one Sensor attached to it.

---

## Rule

- Number: BR-005
- Title: Sensor measurements

### Data constraint

Every Sensor must produce at least one Measurement. Each Measurement must have exactly one numeric measurement value and exactly one unit code.

---

## Rule

- Number: BR-006
- Title: Maintenance task asset and due date

### Data constraint

Every MaintenanceTask must be linked to exactly one Asset and must have exactly one due date.

---

## Rule

- Number: BR-007
- Title: Maintenance task priority and assignment

### Data constraint

Every MaintenanceTask must have exactly one priority level and must be assigned to exactly one Operator.

---

## Rule

- Number: BR-008
- Title: Inspection target and date

### Data constraint

Every Inspection must identify exactly one inspected Asset and exactly one inspection date.

---

## Rule

- Number: BR-009
- Title: Work order composition

### Data constraint

Every WorkOrder must contain at least one MaintenanceTask and must have exactly one responsible Department.

---

## Rule

- Number: BR-010
- Title: Critical asset risk controls

### Data constraint

Every CriticalAsset must have exactly one risk level and at least one required Certification.

---

## Rule

- Number: BR-011
- Title: Factory code traceability

### Data constraint

Each registered item must store the unique factory code stamped on the equipment.

---

## Rule

- Number: BR-012
- Title: Intervention deadline

### Data constraint

Every planned job must include the calendar deadline by which the intervention has to be finished.

---

## Rule

- Number: BR-013
- Title: Field review record

### Data constraint

Each field review must state when it took place and the equipment that was reviewed.
