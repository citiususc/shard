# Generation context

## Domain context

The ontology models an asset maintenance domain. Assets may be machines or critical assets, can be installed at sites, and are managed through inspections, work orders, and maintenance tasks. Sensors attached to machines produce numeric measurements with unit codes. Maintenance tasks are assigned to operators, linked to assets, prioritized with controlled priority levels, and grouped in work orders owned by departments. Critical assets require explicit risk classification and maintenance certifications.

## SHACL generation guidance

Generate compact SHACL NodeShapes using the ontology vocabulary. Prefer sh:targetClass for class-level rules. Use sh:path with the exact ontology property mentioned by the rule. Add sh:minCount and sh:maxCount for cardinality constraints such as “exactly one” or “at least one”. Use sh:class for object properties whose values must be instances of a target class, and sh:datatype for datatype properties such as strings, dates, and decimals. Add clear sh:message values explaining the violated business rule.
