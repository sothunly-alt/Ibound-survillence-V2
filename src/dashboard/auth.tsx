import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { Session, User } from "@supabase/supabase-js";
import { supabase, supabaseConfigured } from "../lib/supabase";
import {
  deleteCamera as deleteCameraRow,
  deleteCrew as deleteCrewRow,
  loadAccount,
  replaceRois as replaceRoiRows,
  updateProfile as updateProfileRow,
  uploadAvatar as uploadAvatarFile,
  uploadCrewPhoto as uploadCrewPhotoFile,
  upsertCamera as upsertCameraRow,
  upsertCrew as upsertCrewRow,
  type AccountSnapshot,
  type CameraInput,
  type CrewRow,
  type ProfileRow,
  type RoiInput,
} from "./account";

type AuthStatus = "loading" | "ready";

type AccountApi = {
  configured: boolean;
  status: AuthStatus;
  session: Session | null;
  user: User | null;
  snapshot: AccountSnapshot | null;
  error: string | null;
  passwordRecovery: boolean;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
  beginPasswordRecovery: () => void;
  finishPasswordRecovery: () => void;
  updateProfile: (patch: Pick<ProfileRow, "display_name" | "venue_name"> & { setup_completed?: boolean }) => Promise<void>;
  uploadAvatar: (file: File) => Promise<void>;
  saveCamera: (input: CameraInput) => Promise<void>;
  removeCamera: (id: string) => Promise<void>;
  saveRois: (bays: RoiInput[]) => Promise<void>;
  saveCrew: (input: { id?: string; display_name: string; role?: string; photo?: File | null }) => Promise<void>;
  removeCrew: (crew: CrewRow) => Promise<void>;
};

const AccountContext = createContext<AccountApi | null>(null);

export function AccountProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [session, setSession] = useState<Session | null>(null);
  const [snapshot, setSnapshot] = useState<AccountSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [passwordRecovery, setPasswordRecovery] = useState(false);

  useEffect(() => {
    if (!supabaseConfigured) {
      setStatus("ready");
      return;
    }
    let cancelled = false;
    void supabase.auth.getSession().then(({ data }) => {
      if (cancelled) return;
      setSession(data.session);
      setStatus("ready");
    });
    const { data } = supabase.auth.onAuthStateChange((event, next) => {
      setSession(next);
      if (event === "PASSWORD_RECOVERY") setPasswordRecovery(true);
    });
    return () => {
      cancelled = true;
      data.subscription.unsubscribe();
    };
  }, []);

  const refresh = useCallback(async () => {
    const user = session?.user;
    if (!user) {
      setSnapshot(null);
      return;
    }
    try {
      const data = await loadAccount(user);
      setSnapshot(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load account.");
    }
  }, [session?.user?.id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const api = useMemo<AccountApi>(() => {
    const user = session?.user ?? null;
    return {
      configured: supabaseConfigured,
      status,
      session,
      user,
      snapshot,
      error,
      passwordRecovery,
      refresh,
      signOut: async () => {
        await supabase.auth.signOut();
        setSnapshot(null);
        setPasswordRecovery(false);
      },
      finishPasswordRecovery: () => setPasswordRecovery(false),
      beginPasswordRecovery: () => setPasswordRecovery(true),
      updateProfile: async (patch) => {
        if (!user) return;
        const profile = await updateProfileRow(user.id, patch);
        setSnapshot((prev) => (prev ? { ...prev, profile } : prev));
      },
      uploadAvatar: async (file) => {
        if (!user || !snapshot) return;
        const result = await uploadAvatarFile(user, file, snapshot.profile.avatar_path);
        setSnapshot((prev) => (prev ? { ...prev, profile: result.profile, avatarUrl: result.avatarUrl } : prev));
      },
      saveCamera: async (input) => {
        if (!user) return;
        await upsertCameraRow(user.id, input);
        await refresh();
      },
      removeCamera: async (id) => {
        if (!user) return;
        await deleteCameraRow(user.id, id);
        await refresh();
      },
      saveRois: async (bays) => {
        if (!user) return;
        const rois = await replaceRoiRows(user.id, bays);
        setSnapshot((prev) => (prev ? { ...prev, rois } : prev));
      },
      saveCrew: async (input) => {
        if (!user) return;
        const crew = await upsertCrewRow(user.id, input);
        if (input.photo) await uploadCrewPhotoFile(user, crew, input.photo);
        await refresh();
      },
      removeCrew: async (crew) => {
        if (!user) return;
        await deleteCrewRow(user.id, crew);
        await refresh();
      },
    };
  }, [error, passwordRecovery, refresh, session, snapshot, status]);

  return createElement(AccountContext.Provider, { value: api }, children);
}

export function useAccount(): AccountApi {
  const ctx = useContext(AccountContext);
  if (!ctx) throw new Error("useAccount must be used inside AccountProvider");
  return ctx;
}
