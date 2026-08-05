/* One-off vendor-time extraction of Carbon design token data to JSON.
   Run: node extract.cjs <path-to-color-palette.scss> <outdir> */
const fs = require('fs');
const path = require('path');
const themes = require('@carbon/themes');
const carbonColors = require('@carbon/colors');
const type = require('@carbon/type');
const layout = require('@carbon/layout');

const [, , scssPath, outDir] = process.argv;
fs.mkdirSync(outDir, { recursive: true });

function writeJson(name, data) {
  fs.writeFileSync(path.join(outDir, name), JSON.stringify(data, null, 2) + '\n');
  console.log('wrote', name);
}

/* 1. g90 theme: plain token -> value map */
writeJson('g90.json', themes.g90);

/* 2. color scales: nested { hue: { grade: hex } } */
writeJson('colors.json', carbonColors.colors);

/* 3. type: named styles + families + weights + scale */
const styleNames = Object.keys(type).filter(
  (k) => type[k] && typeof type[k] === 'object' && 'fontSize' in type[k]
);
const styles = {};
for (const name of styleNames.sort()) styles[name] = type[name];
writeJson('type.json', {
  fontFamilies: type.fontFamilies,
  fontWeights: type.fontWeights,
  scale: type.scale,
  styles,
});

/* 4. layout: spacing, containers, icon sizes, breakpoints */
writeJson('layout.json', {
  baseFontSize: layout.baseFontSize,
  miniUnit: layout.miniUnit,
  spacing: layout.spacing,
  fluidSpacing: layout.fluidSpacing,
  container: layout.container,
  iconSize: layout.iconSize,
  sizes: layout.sizes,
  breakpoints: layout.breakpoints,
});

/* 5. data-viz palettes: parse @carbon/charts _color-palette.scss */
let scss = fs.readFileSync(scssPath, 'utf8');
scss = scss.replace(/\/\/[^\n]*/g, '');
scss = scss.replace(/@use[^\n]*\n/g, '');
scss = scss.replace(/@function[\s\S]*?\n}\n/, '');
scss = scss.replace(/#([0-9a-fA-F]{3,8})\b/g, '"#$1"');
scss = scss.replace(/getColorValue\((\w+),\s*(\d+)\)/g, (m, hue, grade) => {
  const value = carbonColors.colors[hue] && carbonColors.colors[hue][grade];
  if (!value) throw new Error('unresolved color ' + hue + ' ' + grade);
  return '"' + value + '"';
});
const result = {};
const blockRe = /\$([\w-]+):\s*(\(([\s\S]*?)\)|"[^"]*");/g;
let m;
while ((m = blockRe.exec(scss)) !== null) {
  const name = m[1];
  let body = m[2];
  if (body.startsWith('"')) {
    result[name] = JSON.parse(body);
    continue;
  }
  const json = body.replace(/\(/g, '{').replace(/\)/g, '}').replace(/'/g, '"');
  result[name] = JSON.parse(json);
}
writeJson('dataviz.json', result);
console.log('dataviz keys:', Object.keys(result).join(', '));
console.log('fontWeights:', JSON.stringify(type.fontWeights));
