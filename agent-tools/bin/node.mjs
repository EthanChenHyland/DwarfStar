#!/usr/bin/env node
// ESM resolves from the user's script location; bridge only the two installed harnesses.
import {registerHooks} from 'node:module';
import {resolve as toolResolve} from '../lib/loader.mjs';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
registerHooks({resolve:toolResolve});
const file=process.argv[2];if(!file)throw new Error('Usage: dwarf-node workspace-script.mjs [args]');
process.argv=[process.argv[0],resolve(file),...process.argv.slice(3)];
await import(pathToFileURL(resolve(file)).href);
