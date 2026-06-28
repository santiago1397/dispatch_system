UPDATE companies
SET identification_patterns = $json$[
  {"patterns": [
    "(?m)^Confirm:\\s+pd\\.getjobox\\.com/[a-z0-9]{5}\\s*$",
    "(?m)^Ref:\\s+(?:Usafe\\s+Locksmith|Us\\s+Garage\\s+Door)\\s*$",
    "(?m)^PDL:\\s+[A-Z0-9]{5}\\s*$",
    "(?m)^N:\\s+",
    "(?m)^Ph:\\s+\\d{10}",
    "(?m)^Addr:\\s+",
    "(?m)^Desc:\\s+",
    "(?m)^Occu:\\s+",
    "(?m)^Estimate:\\s+"
  ]},
  {"patterns": [
    "(?m)^We\\s+Answer\\s+-\\s+New\\s+Job\\s*$",
    "(?m)^job-\\d{7}\\s*$",
    "(?m)^(?:Garage\\s+door|locksmith)\\s+service\\s+\\(MB\\)\\s*$",
    "(?m)^Name:\\s+",
    "(?m)^Phone:\\s+\\+?1?\\d{10}",
    "(?m)^Address:\\s+",
    "(?m)^Job\\s+Type:\\s+",
    "(?m)^Notes:\\s*"
  ]},
  {"patterns": [
    "(?m)^Ref:\\s+Usafe\\s+Locksmith\\s*$"
  ]},
  {"patterns": [
    "(?m)^Ref:\\s+Us\\s+Garage\\s+Door\\s*$"
  ]}
]$json$::jsonb
WHERE name = 'USAFE';

SELECT name, jsonb_pretty(identification_patterns) FROM companies WHERE name = 'USAFE';
