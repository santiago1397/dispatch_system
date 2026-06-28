UPDATE companies
SET identification_patterns = $json$[
  {"patterns": [
    "(?m)^A\\.S\\.A\\.P\\s+Services\\s*$",
    "(?m)^(?:Job\\s+)?ID:\\s+#[A-Z0-9]{7}\\s*$",
    "(?m)^Name:\\s+",
    "(?m)^Phone:\\s+(?:\\+1\\s+)?\\(?\\d{3}\\)?[\\s.-]?\\d{3}[\\s.-]?\\d{4}",
    "(?m)^Address:\\s+.+,\\s*United\\s+States\\s*$",
    "(?m)^Job:\\s+"
  ]},
  {"patterns": [
    "(?m)^A\\.S\\.A\\.P\\s+Services\\s*$",
    "(?m)^(?:Job\\s+)?ID:\\s+#[A-Z0-9]{7}\\s*$"
  ]},
  {"patterns": [
    "(?m)^A\\.S\\.A\\.P\\s+Services\\s*$",
    "(?m)^Address:\\s+.+,\\s*United\\s+States\\s*$",
    "(?m)^Job:\\s+"
  ]}
]$json$::jsonb
WHERE name = 'ASAP_LOCKSMITH';

SELECT name, jsonb_pretty(identification_patterns) FROM companies WHERE name = 'ASAP_LOCKSMITH';
