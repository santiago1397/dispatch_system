UPDATE companies
SET identification_patterns = $json$[
  {"patterns": [
    "\\bNew\\s+job\\s+#\\s*[A-Z0-9]{6}\\b",
    "\\(2037699944\\s+#\\d+\\)",
    ",\\s*Illinois\\s+\\d{5}",
    "(?m)^Notes:\\s+#"
  ]},
  {"patterns": [
    "\\(2037699944\\s+#\\d+\\)"
  ]},
  {"patterns": [
    "\\bNew\\s+job\\s+#\\s*[A-Z0-9]{6}\\b",
    "(?m)^Notes:\\s+#"
  ]},
  {"patterns": [
    "New\\sjob\\s#[A-Z0-9]{6}",
    "\\(\\d{10}\\s#\\d+\\)",
    "Notes:"
  ]},
  {"patterns": [
    "New\\sjob\\s#[A-Z0-9]{6}",
    "Service Confirm:",
    "Notes: #"
  ]}
]$json$::jsonb
WHERE name = 'SLK';

SELECT name, jsonb_pretty(identification_patterns) FROM companies WHERE name = 'SLK';
