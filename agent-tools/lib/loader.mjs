export function resolve(specifier,context,nextResolve){
  if(['playwright','puppeteer'].includes(specifier))return nextResolve(specifier,{...context,parentURL:new URL('../package.json',import.meta.url).href});
  return nextResolve(specifier,context);
}
