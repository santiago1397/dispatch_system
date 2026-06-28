UPDATE companies
SET identification_patterns = $json$[
  {"patterns": [
    "Confirm:\\s+https?://s1j\\.co/j/[A-Z0-9]{6}\\b",
    "\\bNew\\s+(?:lead|job)\\s+#\\s*\\d+\\b",
    "\\(\\d{10}\\)"
  ]},
  {"patterns": [
    "https?://s1j\\.co/j/[A-Z0-9]{6}\\b"
  ]},
  {"patterns": [
    "J#\\d{10}",
    "\\|\\s\\d{2}/\\d{2}/\\d{4}"
  ]},
  {"patterns": [
    "Professional Locksmith"
  ]},
  {"patterns": [
    "(?:.*\\n){0,1}.*PROFESSIONAL.*(?:\\n|)$"
  ]},
  {"patterns": [
    "E#\\d{10}",
    "\\|\\s\\d{2}/\\d{2}/\\d{4}"
  ]},
  {"patterns": [
    "J#\\d{10}"
  ]}
]$json$::jsonb
WHERE name = 'PROFESSIONAL_LOCKSMITH';

SELECT name, jsonb_pretty(identification_patterns) FROM companies WHERE name = 'PROFESSIONAL_LOCKSMITH';
