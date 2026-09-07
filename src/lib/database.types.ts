export type Json = string | number | boolean | null | { [key: string]: Json | undefined } | Json[];

export type Database = {
  public: {
    Tables: {
      cameras: {
        Row: {
          id: string;
          user_id: string;
          external_id: string | null;
          name: string;
          zone: string;
          protocol: "webcam" | "rtsp" | "phone" | "onvif" | "tapo" | "webrtc";
          source_url: string;
          main_source_url: string;
          username: string;
          password: string;
          vendor: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          user_id: string;
          external_id?: string | null;
          name: string;
          zone?: string;
          protocol?: "webcam" | "rtsp" | "phone" | "onvif" | "tapo" | "webrtc";
          source_url?: string;
          main_source_url?: string;
          username?: string;
          password?: string;
          vendor?: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          user_id?: string;
          external_id?: string | null;
          name?: string;
          zone?: string;
          protocol?: "webcam" | "rtsp" | "phone" | "onvif" | "tapo" | "webrtc";
          source_url?: string;
          main_source_url?: string;
          username?: string;
          password?: string;
          vendor?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      crew_identities: {
        Row: {
          id: string;
          user_id: string;
          display_name: string;
          role: string;
          photo_path: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          user_id: string;
          display_name: string;
          role?: string;
          photo_path?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          user_id?: string;
          display_name?: string;
          role?: string;
          photo_path?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      profiles: {
        Row: {
          id: string;
          display_name: string;
          venue_name: string;
          avatar_path: string | null;
          setup_completed: boolean;
          telegram_chat_id: string | null;
          telegram_user_id: string | null;
          telegram_username: string | null;
          telegram_linked_at: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id: string;
          display_name?: string;
          venue_name?: string;
          avatar_path?: string | null;
          setup_completed?: boolean;
          telegram_chat_id?: string | null;
          telegram_user_id?: string | null;
          telegram_username?: string | null;
          telegram_linked_at?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          display_name?: string;
          venue_name?: string;
          avatar_path?: string | null;
          setup_completed?: boolean;
          telegram_chat_id?: string | null;
          telegram_user_id?: string | null;
          telegram_username?: string | null;
          telegram_linked_at?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      roi_bays: {
        Row: {
          id: string;
          user_id: string;
          camera_id: string | null;
          external_id: string | null;
          name: string;
          bay_type: "vehicle_bay" | "tool_area";
          roi: number[];
          sort_order: number;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          user_id: string;
          camera_id?: string | null;
          external_id?: string | null;
          name: string;
          bay_type?: "vehicle_bay" | "tool_area";
          roi: number[];
          sort_order?: number;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          user_id?: string;
          camera_id?: string | null;
          external_id?: string | null;
          name?: string;
          bay_type?: "vehicle_bay" | "tool_area";
          roi?: number[];
          sort_order?: number;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
    };
    Views: Record<string, never>;
    Functions: Record<string, never>;
    Enums: Record<string, never>;
    CompositeTypes: Record<string, never>;
  };
};
