UPDATE companies
SET identification_patterns = $json$[
  {"patterns": [
    "(?m)^A1\\s+Locksmith\\s*$",
    "(?m)^JOB\\s+ID:\\s+[0-9A-F]{6,8}\\s*$",
    "(?m)^Source:\\s+Agency\\s*$",
    "(?m)^Address:\\s+",
    "(?m)^Phone:\\(?\\d{3}\\)?[-.\\s]?\\d{3}[-.\\s]?\\d{4}",
    "(?m)^Job\\s+Type:\\s+",
    "(?m)^Description:\\s+"
  ]},
  {"patterns": [
    "(?m)^A1\\s+Locksmith\\s*$",
    "(?m)^Source:\\s+Agency\\s*$"
  ]}
]$json$::jsonb
WHERE name = 'A1_LOCKSMITH';

SELECT name, jsonb_pretty(identification_patterns) FROM companies WHERE name = 'A1_LOCKSMITH';
