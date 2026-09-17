export type MainChatSkinDevice = 'desktop' | 'tablet' | 'mobile';

export type MainChatSkinAsset = {
  key: string;
  path: string;
  mime: 'image/png' | 'image/jpeg' | 'image/webp';
  sha256: string;
  url?: string;
};

export type MainChatSkinDecoration = {
  asset: string;
  anchor:
    | 'page-top-left'
    | 'page-top-right'
    | 'page-bottom-left'
    | 'page-bottom-right'
    | 'intro-top'
    | 'composer-top-left'
    | 'composer-top-right';
  width: number;
  x: number;
  y: number;
  opacity: number;
  visible: boolean;
};

export type MainChatSkinLayout = {
  background: {
    asset?: string;
    fit: 'cover' | 'contain';
    position: 'center' | 'center-top' | 'center-bottom' | 'left-bottom' | 'right-bottom';
    opacity: number;
  };
  content: {
    maxWidth: number;
    topGap: number;
  };
  decorations: MainChatSkinDecoration[];
};

export type MainChatSkinManifest = {
  kind: 'axiom-main-chat-skin';
  scope: 'main_chat';
  schemaVersion: 1;
  key: string;
  version: string;
  name: string;
  description: string;
  renderer: 'decorated-chat-v1';
  assets: MainChatSkinAsset[];
  theme: {
    colors: Partial<Record<
      'page' | 'title' | 'body' | 'accent' | 'composer' | 'composerBorder' | 'userBubble' | 'userBubbleText',
      string
    >>;
    composer: {
      radius?: number;
      shadow?: 'none' | 'soft' | 'elevated';
    };
  };
  layouts: Record<MainChatSkinDevice, MainChatSkinLayout>;
};

export type MainChatSkinRecord = {
  id: string;
  scope: 'main_chat';
  key: string;
  version: string;
  schemaVersion: number;
  name: string;
  description: string;
  renderer: string;
  contentHash: string;
  sourceType: 'builtin' | 'imported' | string;
  status: 'active' | 'incomplete' | 'disabled' | string;
  installedBy?: string;
  createdAt?: string | null;
  updatedAt?: string | null;
  referenceCount?: number;
  deletable?: boolean;
  manifest: MainChatSkinManifest | null;
};

export type HydratedMainChatSkin = MainChatSkinRecord & {
  assetUrls: Record<string, string>;
};

export type MainChatSkinRuntimeResponse = {
  scope: 'main_chat';
  skin: MainChatSkinRecord | null;
};
