# Example 1

## Generated

```ttl
era-sh:GprsImplementationArea
    a sh:PropertyShape ;
    era:affectedProperty <http://data.europa.eu/949/gprsImplementationArea> ;
    era:affectedClass <http://data.europa.eu/949/CommonCharacteristicsSubset> ;
    era:affectedClass <http://data.europa.eu/949/RunningTrack> ;
    era:scope "local" ;
    era:rinfIndex "1.1.1.3.3.3.3" ;
    era:rinfIndex "1.2.1.1.2.3.3" ;
    rdfs:comment "Indication of the area in which GPRS can be used for ETCS, expressed as a list of GPRS-enabled RBCs."@en ;
    sh:path <http://data.europa.eu/949/gprsImplementationArea> ;
    sh:datatype xsd:string ;
    sh:severity sh:Violation ;
    sh:message "gprsImplementationArea (1.1.1.3.3.3.3, 1.2.1.1.2.3.3): The gprsImplementationArea must be a string. This error is due to the value not being a string."@en .
```

## Gold

```ttl
era-sh:GprsImplementationArea
	a sh:PropertyShape ;
    era:affectedProperty era:gprsImplementationArea;
    era:affectedClass era:RunningTrack;
    era:scope "local";
	rdfs:comment "Indication of the area in which GPRS can be used for ETCS, expressed as a list of GPRS-enabled RBC's. "@en ;
	era:rinfIndex "1.1.1.3.3.3.3" , "1.2.1.1.2.3.3" ;
	sh:path era:gprsImplementationArea ;
	sh:datatype xsd:string ;
	sh:severity sh:Violation ;
	sh:message "gprsImplementationArea (1.1.1.3.3.3.3, 1.2.1.1.2.3.3): The gprsImplementationArea must be a string. This error is due to the value not being a string."@en .

era-sh:GprsImplementationAreaApplicability
	a sh:SPARQLConstraint ;
    era:affectedProperty era:gprsImplementationArea;
    era:affectedClass era:RunningTrack;
    era:scope "local";
	rdfs:comment "GSM-R (parameter 1.1.1.3.3.1), ETCS L2 (parameter 1.1.1.3.2.1) and GPRS for ETCS (parameter 1.1.1.3.3.3.2) must be installed for this parameter to be applicable. "@en ;
	era:rinfIndex "1.1.1.3.3.3.3" , "1.2.1.1.2.3.3" ;
	sh:message "gprsImplementationArea (1.1.1.3.3.3.3, 1.2.1.1.2.3.3):The track {$this} ({?label}), has a 'GSM-R version' defined and a 'ETCS level' type selected which makes the gprsImplementationArea parameter applicable. This error is due to {$this} not having a value for such a parameter."@en ;
    sh:prefixes era:;
	sh:select """
    PREFIX era: <http://data.europa.eu/949/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT $this ?label (era:gprsImplementationArea AS ?path)
			WHERE {
                 $this era:gsmRVersion ?gsmRVersion;
				 	   era:gprsForETCS true;
				 	   era:etcs ?etcs.
				 ?etcs era:etcsLevelType ?etcsType.
        OPTIONAL { $this rdfs:label ?label0 } .
        BIND(COALESCE(?label0, "unknown label") AS ?label)
                FILTER NOT EXISTS {$this era:gprsImplementationArea ?value} . 
            }
			""" .
```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

# Example 1

## Generated

```ttl

```

## Gold

```ttl

```

---

