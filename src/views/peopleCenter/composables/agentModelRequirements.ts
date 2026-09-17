export function parseUseModels(value: unknown): string[] {
  if (typeof value !== 'string') return [];
  return Array.from(new Set(value.split(',').map((model) => model.trim()).filter(Boolean)));
}

export function getMissingModels(requiredModels: unknown, availableModels: Iterable<string>): string[] {
  const available = new Set(Array.from(availableModels, (model) => model.trim()).filter(Boolean));
  return parseUseModels(requiredModels).filter((model) => !available.has(model));
}

export function decorateAppsWithModelAvailability(apps: any[], availableModels: Iterable<string> | null): any[] {
  return apps.map((item) => {
    const { missingModels: _missingModels, ...app } = item || {};
    if (!availableModels) return app;

    const requiredModels = [app.useModels, app.use_models]
      .filter((value) => typeof value === 'string')
      .join(',');
    const missingModels = getMissingModels(requiredModels, availableModels);
    return missingModels.length ? { ...app, missingModels } : app;
  });
}
