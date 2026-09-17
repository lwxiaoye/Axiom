import {translations,newTrans} from "./zh";
 
// let zh = translations
let zh = newTrans
export default function customTranslate(template:any, replacements:any) {
  replacements = replacements || {};
 
  // Translate
  template = zh[template] || template;
 
  // Replace
  return template.replace(/{([^}]+)}/g, function(_:any, key:any) {
    let str = replacements[key];
    if (
      zh[replacements[key]] !== null &&
      zh[replacements[key]] !== "undefined"
    ) {
      // eslint-disable-next-line no-mixed-spaces-and-tabs
      str = zh[replacements[key]];
      // eslint-disable-next-line no-mixed-spaces-and-tabs
    }
    return str || "{" + key + "}";
  });
}