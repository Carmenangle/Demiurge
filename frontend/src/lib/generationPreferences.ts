import { useCallback, useEffect, useState } from "react";
import {
  ASPECTS, IMAGE_QUALITIES, RES_TIERS, normalizeCustomDimension, type ImageQuality,
} from "./viewRouting";

const KEY = "laf_generation_sizes";
const DEFAULT_SIZE = {
  aspect: "1:1",
  resTier: "1k",
  quality: "high" as ImageQuality,
  customEnabled: false,
  customWidth: 1280,
  customHeight: 1280,
};

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

type StoredSizes = Record<string, {
  aspect?: string;
  resTier?: string;
  quality?: string;
  customEnabled?: boolean;
  customWidth?: number;
  customHeight?: number;
}>;

function read(storage: StorageLike): StoredSizes {
  try {
    const value = JSON.parse(storage.getItem(KEY) || "{}");
    return value && typeof value === "object" ? value as StoredSizes : {};
  } catch {
    return {};
  }
}

export function loadGenerationSize(storage: StorageLike, repoId: string) {
  const saved = read(storage)[repoId] || {};
  return {
    aspect: ASPECTS.includes(saved.aspect || "") ? saved.aspect! : DEFAULT_SIZE.aspect,
    resTier: Object.prototype.hasOwnProperty.call(RES_TIERS, saved.resTier || "") ? saved.resTier! : DEFAULT_SIZE.resTier,
    quality: Object.prototype.hasOwnProperty.call(IMAGE_QUALITIES, saved.quality || "")
      ? saved.quality as ImageQuality
      : DEFAULT_SIZE.quality,
    customEnabled: saved.customEnabled === true,
    customWidth: normalizeCustomDimension(saved.customWidth, DEFAULT_SIZE.customWidth),
    customHeight: normalizeCustomDimension(saved.customHeight, DEFAULT_SIZE.customHeight),
  };
}

export function saveGenerationSize(
  storage: StorageLike,
  repoId: string,
  aspect: string,
  resTier: string,
  quality: ImageQuality = DEFAULT_SIZE.quality,
  customEnabled = DEFAULT_SIZE.customEnabled,
  customWidth = DEFAULT_SIZE.customWidth,
  customHeight = DEFAULT_SIZE.customHeight,
) {
  const current = read(storage);
  current[repoId] = {
    aspect: ASPECTS.includes(aspect) ? aspect : DEFAULT_SIZE.aspect,
    resTier: Object.prototype.hasOwnProperty.call(RES_TIERS, resTier) ? resTier : DEFAULT_SIZE.resTier,
    quality: Object.prototype.hasOwnProperty.call(IMAGE_QUALITIES, quality) ? quality : DEFAULT_SIZE.quality,
    customEnabled: customEnabled === true,
    customWidth: normalizeCustomDimension(customWidth, DEFAULT_SIZE.customWidth),
    customHeight: normalizeCustomDimension(customHeight, DEFAULT_SIZE.customHeight),
  };
  storage.setItem(KEY, JSON.stringify(current));
}

export function useGenerationPreferences(repoId: string, storage: StorageLike = localStorage) {
  const [preferences, setPreferences] = useState(() => loadGenerationSize(storage, repoId));

  useEffect(() => {
    setPreferences(loadGenerationSize(storage, repoId));
  }, [repoId, storage]);

  const update = useCallback((aspect: string, resTier: string, quality: ImageQuality) => {
    setPreferences((current) => {
      const next = { ...current, aspect, resTier, quality };
      saveGenerationSize(storage, repoId, next.aspect, next.resTier, next.quality,
        next.customEnabled, next.customWidth, next.customHeight);
      return next;
    });
  }, [repoId, storage]);

  const updateCustom = useCallback((customEnabled: boolean, customWidth: number, customHeight: number) => {
    setPreferences((current) => {
      const next = {
        ...current,
        customEnabled,
        customWidth: normalizeCustomDimension(customWidth, current.customWidth),
        customHeight: normalizeCustomDimension(customHeight, current.customHeight),
      };
      saveGenerationSize(storage, repoId, next.aspect, next.resTier, next.quality,
        next.customEnabled, next.customWidth, next.customHeight);
      return next;
    });
  }, [repoId, storage]);

  return { ...preferences, update, updateCustom };
}
