UPDATE companies
SET identification_patterns = $json$[
  {"patterns": [
    "(?m)^New\\s+Lead\\s+#[A-Z0-9]{6}\\s*$",
    "(?m)^Company:\\s+American\\s+Locksmith\\s*$",
    "(?m)^Contact:\\s+",
    "(?m)^Phone:\\s+\\+16506754074(?:\\s+#\\d+)?",
    "(?m)^Address:\\s+",
    "(?m)^Job\\s+Type:\\s+",
    "(?m)^Notes:\\s*",
    "(?m)^Start:\\s+\\d{2}/\\d{2}/\\d{4}\\s+\\d{1,2}:\\d{2}\\s*[AP]M\\s+-\\s+End:\\s+\\d{2}/\\d{2}/\\d{4}\\s+\\d{1,2}:\\d{2}\\s*[AP]M\\s*$"
  ]},
  {"patterns": [
    "New\\sjob\\s#[A-Z0-9]{6}",
    "Locksmith\\s24/7",
    "\\(\\d{10}\\s#\\d+\\)"
  ]},
  {"patterns": [
    "New job #",
    "Locksmith 24/7",
    "Please send OK to confirm"
  ]},
  {"patterns": [
    "(?m)^Phone:\\s+\\+16506754074\\s+#\\d+\\s*$"
  ]},
  {"patterns": [
    "(?m)^Company:\\s+American\\s+Locksmith\\s*$",
    "(?m)^New\\s+Lead\\s+#[A-Z0-9]{6}\\s*$"
  ]}
]$json$::jsonb
WHERE name = 'SHAHAF';

SELECT name, jsonb_pretty(identification_patterns) FROM companies WHERE name = 'SHAHAF';
