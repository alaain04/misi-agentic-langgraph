/** @type {import('httpyac').HttpyacHooksApi} */
module.exports = {
  configureHooks: (api) => {
    // Log response status for every request
    api.hooks.responseLogging.addHook('logStatus', (response) => {
      console.log(`→ ${response.statusCode} ${response.statusMessage}`);
    });
  },
};
