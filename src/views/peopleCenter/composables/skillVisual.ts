/**
 * 技能的图标 + 品牌色（2026-07-28 从 SkillSquare 抽出共享）。
 *
 * Skill 广场的卡片与 composer + 菜单里的「使用技能」面板展示的是同一批技能，图标必须是
 * 同一套——用户在广场认得的那个橙色 PPT 方块，到了对话框里也得是它。
 *
 * 判据只看 name/skillId（技能身份字段，稳定可预测），**不扫描描述**——描述里出现
 * 「document」「表格」这类词会把无关技能误判成 Word/Excel。
 */
import type { Component } from 'vue';
import {
  BgColorsOutlined,
  FileExcelOutlined,
  FilePdfOutlined,
  FilePptOutlined,
  FileWordOutlined,
  PieChartOutlined,
  ScheduleOutlined,
  SoundOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from '@ant-design/icons-vue';

export type SkillVisual = { icon: Component; bg: string };

export const SKILL_VISUALS: Array<{ re: RegExp; icon: Component; bg: string }> = [
  { re: /ppt|powerpoint|slide|deck|presentation|幻灯|演示/, icon: FilePptOutlined, bg: 'linear-gradient(135deg, #e8632a 0%, #c43e1c 100%)' },
  { re: /xls|excel|spreadsheet|csv|workbook|sheet|表格/, icon: FileExcelOutlined, bg: 'linear-gradient(135deg, #21a366 0%, #167544 100%)' },
  { re: /docx?|word|文档/, icon: FileWordOutlined, bg: 'linear-gradient(135deg, #3a6fc4 0%, #1f4477 100%)' },
  { re: /pdf/, icon: FilePdfOutlined, bg: 'linear-gradient(135deg, #f0563f 0%, #c11e17 100%)' },
  { re: /周报|报告|report|weekly/, icon: ScheduleOutlined, bg: 'linear-gradient(135deg, #14b8a6 0%, #0f766e 100%)' },
  { re: /营销|推广|增长|marketing|growth/, icon: SoundOutlined, bg: 'linear-gradient(135deg, #ec4899 0%, #be185d 100%)' },
  { re: /市场|研究|调研|market|research/, icon: PieChartOutlined, bg: 'linear-gradient(135deg, #f59e0b 0%, #b45309 100%)' },
  { re: /生产力|效率|习惯|productivity/, icon: ThunderboltOutlined, bg: 'linear-gradient(135deg, #06b6d4 0%, #0e7490 100%)' },
  { re: /设计|前端|界面|design|frontend/, icon: BgColorsOutlined, bg: 'linear-gradient(135deg, #d946ef 0%, #a21caf 100%)' },
];

export const DEFAULT_SKILL_VISUAL: SkillVisual = {
  icon: ToolOutlined as Component,
  bg: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
};

/** 只取身份字段：`name` + `skillId` */
export function skillVisualOf(name?: string, skillId?: string): SkillVisual {
  const key = `${name || ''} ${skillId || ''}`.toLowerCase();
  return SKILL_VISUALS.find((v) => v.re.test(key)) ?? DEFAULT_SKILL_VISUAL;
}
