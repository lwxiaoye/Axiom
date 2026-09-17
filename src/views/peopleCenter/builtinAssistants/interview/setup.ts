import type { UploadedFile } from '../../agentApi';
import type { InterviewConfig } from './types';

export function validateInterviewDocument(file: Pick<File, 'name' | 'size'>): string {
  if (!/\.(pdf|docx|txt|md)$/i.test(file.name)) return '请选择 PDF、DOCX、TXT 或 Markdown 文档';
  if (file.size === 0) return '文件是空的，请重新选择';
  return '';
}

export function validateInterviewMaterial(file: UploadedFile | null): string {
  if (!file) return '请先上传简历';
  if (file.uploading) return '请等待文档解析完成';
  if (!file.file_id || file.status === 'failed' || !file.text?.trim()) {
    return file.note || '未能读取文档文字，请换用可复制文字的 PDF、DOCX 或纯文本文档后重新上传';
  }
  return '';
}

export function validateInterviewConfig(config: InterviewConfig): string {
  if (!config.job_title.trim()) return '请填写目标岗位';
  if (!config.resume_file_id) return '请先上传简历';
  if (!config.jd_text.trim() && !config.jd_file_id) return '请粘贴或上传目标岗位 JD';
  if (!Number.isInteger(config.question_count) || config.question_count < 3 || config.question_count > 12) {
    return '本场题量应为 3 到 12 题';
  }
  return '';
}

export function interviewStartMessage(config: InterviewConfig): string {
  const levels = { intern: '实习求职', graduate: '应届求职', experienced: '有工作经验' };
  const pressures = { gentle: '温和练习', normal: '标准面试', challenging: '加强挑战' };
  return `${config.job_title.trim()} · 模拟面试（${config.question_count} 题）\n${levels[config.level]} · ${pressures[config.pressure_level]}。请结合我的简历与岗位要求，一次问一个问题。`;
}
