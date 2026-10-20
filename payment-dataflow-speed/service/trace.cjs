'use strict';
const fs = require('node:fs');
const {AsyncLocalStorage} = require('node:async_hooks');
const context = new AsyncLocalStorage();
let counter = 0;
module.exports = {
  context,
  enter(scope,node) {
    const frame = `javascript:${++counter}`;
    this.event(frame,node,'node'); this.event(frame,node,'def');
    return frame;
  },
  event(frame,node,phase) {
    const test_id = context.getStore();
    if (!test_id) throw new Error('Missing dataflow test correlation ID');
    fs.appendFileSync(process.env.DF_SERVICE_EVENTS,
      JSON.stringify({test_id,frame,node,phase})+'\n');
  }
};

