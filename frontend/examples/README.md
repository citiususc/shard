# Preloaded SHARD examples

This directory contains complete, importable SHARD sessions listed in
`manifest.json`. Add future examples by placing their session JSON here and
appending one manifest entry; the frontend does not contain domain-specific
example logic.

## ePO examples

The ePO sessions use records from the
[NL2SHACL-Dataset ePO subset](https://github.com/DE-TUM/NL2SHACL-Dataset/tree/main/epo-dataset)
at commit `9e94a4e4679f9a52cd6290386f23a332de2f5ff6`.

- `epo-rule-session.json` uses record `epo-6`, an Environmental Emission
  Information constraint combining required controlled values, optional object
  links, cardinalities, and alternative string datatypes.
- `epo-batch-session.json` uses records `epo-1`, `epo-5`, `epo-8`, `epo-12`,
  `epo-19`, `epo-22`, `epo-25`, `epo-32`, `epo-40`, and `epo-50`. Together they
  cover cardinality, datatype, class/range, IRI node-kind, alternative string
  datatypes, and multi-property constraints. Canonical property labels are
  included in each entry title to provide precise grounding metadata while the
  source descriptions remain unchanged.

The embedded ontology combines the eProcurement Ontology 5.2.0 modules and
RDFS restriction modules distributed with that dataset. This keeps the
ontology vocabulary and reference constraints on the same version.

## Attribution and licenses

The NL2SHACL-Dataset is licensed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Its ePO
ontology modules identify the Publications Office of the European Union as
publisher and authorize reuse under the
[European Union Public Licence 1.2](https://joinup.ec.europa.eu/collection/eupl/eupl-text-eupl-12).
The original source identifiers and licenses are also recorded in each
session's `exampleMetadata` field.
