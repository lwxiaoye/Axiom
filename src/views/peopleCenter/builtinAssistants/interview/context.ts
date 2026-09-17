import type { InjectionKey } from 'vue';
import type { InterviewSessionController } from './useInterviewSession';

export const InterviewSessionKey: InjectionKey<InterviewSessionController> = Symbol('InterviewSession');
