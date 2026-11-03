// Read local Career-Ops YAML without changing the user's file.
import fs from 'node:fs';
import yaml from 'js-yaml';
console.log(JSON.stringify(yaml.load(fs.readFileSync(process.argv[2], 'utf8')) || {}));
