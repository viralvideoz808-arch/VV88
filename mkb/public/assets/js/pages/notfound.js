import { boot, $ } from '../core.js';
import { mountBull } from '../bull.js';

await boot();
mountBull($('#bull404'), { zoom: 0.9 });
