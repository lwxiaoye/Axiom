import communicationA from '/@/assets/peopleCenter/agentMarket/communication-a.webp';
import communicationB from '/@/assets/peopleCenter/agentMarket/communication-b.webp';
import contentCreationA from '/@/assets/peopleCenter/agentMarket/content_creation-a.webp';
import contentCreationB from '/@/assets/peopleCenter/agentMarket/content_creation-b.webp';
import dataTableA from '/@/assets/peopleCenter/agentMarket/data_table-a.webp';
import dataTableB from '/@/assets/peopleCenter/agentMarket/data_table-b.webp';
import developerAutomationA from '/@/assets/peopleCenter/agentMarket/developer_automation-a.webp';
import developerAutomationB from '/@/assets/peopleCenter/agentMarket/developer_automation-b.webp';
import documentKnowledgeA from '/@/assets/peopleCenter/agentMarket/document_knowledge-a.webp';
import documentKnowledgeB from '/@/assets/peopleCenter/agentMarket/document_knowledge-b.webp';
import imageMultimediaA from '/@/assets/peopleCenter/agentMarket/image_multimedia-a.webp';
import imageMultimediaB from '/@/assets/peopleCenter/agentMarket/image_multimedia-b.webp';
import otherA from '/@/assets/peopleCenter/agentMarket/other-a.webp';
import otherB from '/@/assets/peopleCenter/agentMarket/other-b.webp';
import planningStructureA from '/@/assets/peopleCenter/agentMarket/planning_structure-a.webp';
import planningStructureB from '/@/assets/peopleCenter/agentMarket/planning_structure-b.webp';
import type { AgentCapabilityKey } from './agentMarketCapabilities';

export const AGENT_CAPABILITY_WATERMARKS: Record<AgentCapabilityKey, readonly [string, string]> = {
  communication: [communicationA, communicationB],
  document_knowledge: [documentKnowledgeA, documentKnowledgeB],
  data_table: [dataTableA, dataTableB],
  content_creation: [contentCreationA, contentCreationB],
  planning_structure: [planningStructureA, planningStructureB],
  developer_automation: [developerAutomationA, developerAutomationB],
  image_multimedia: [imageMultimediaA, imageMultimediaB],
  other: [otherA, otherB],
};
