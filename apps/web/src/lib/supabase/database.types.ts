export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.5"
  }
  public: {
    Tables: {
      agent_effort_daily: {
        Row: {
          cost_usd: number
          day: string
          files_changed: number
          issues_completed: number
          lines_added: number
          lines_removed: number
          org_id: string
          runs_finished: number
          tokens_in: number
          tokens_out: number
          updated_at: string
          work_seconds: number
          worker_id: string
        }
        Insert: {
          cost_usd?: number
          day: string
          files_changed?: number
          issues_completed?: number
          lines_added?: number
          lines_removed?: number
          org_id: string
          runs_finished?: number
          tokens_in?: number
          tokens_out?: number
          updated_at?: string
          work_seconds?: number
          worker_id: string
        }
        Update: {
          cost_usd?: number
          day?: string
          files_changed?: number
          issues_completed?: number
          lines_added?: number
          lines_removed?: number
          org_id?: string
          runs_finished?: number
          tokens_in?: number
          tokens_out?: number
          updated_at?: string
          work_seconds?: number
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "agent_effort_daily_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_effort_daily_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_events: {
        Row: {
          actor_email: string
          actor_id: string | null
          created_at: string
          id: string
          org_id: string
          payload: Json
          principal_id: string
          type: string
        }
        Insert: {
          actor_email?: string
          actor_id?: string | null
          created_at?: string
          id?: string
          org_id: string
          payload?: Json
          principal_id: string
          type: string
        }
        Update: {
          actor_email?: string
          actor_id?: string | null
          created_at?: string
          id?: string
          org_id?: string
          payload?: Json
          principal_id?: string
          type?: string
        }
        Relationships: [
          {
            foreignKeyName: "agent_events_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_events_principal_id_fkey"
            columns: ["principal_id"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_failures: {
        Row: {
          category: string
          created_at: string
          detail: Json
          error: string | null
          id: number
          issue_id: string | null
          kind: string
          org_id: string
          preset_name: string | null
          preset_version: number | null
          project_id: string | null
          resumable: boolean
          run_id: string | null
          status: string
          worker_id: string | null
          worker_name: string
          worker_type: string
        }
        Insert: {
          category: string
          created_at?: string
          detail?: Json
          error?: string | null
          id?: never
          issue_id?: string | null
          kind: string
          org_id: string
          preset_name?: string | null
          preset_version?: number | null
          project_id?: string | null
          resumable?: boolean
          run_id?: string | null
          status?: string
          worker_id?: string | null
          worker_name?: string
          worker_type?: string
        }
        Update: {
          category?: string
          created_at?: string
          detail?: Json
          error?: string | null
          id?: never
          issue_id?: string | null
          kind?: string
          org_id?: string
          preset_name?: string | null
          preset_version?: number | null
          project_id?: string | null
          resumable?: boolean
          run_id?: string | null
          status?: string
          worker_id?: string | null
          worker_name?: string
          worker_type?: string
        }
        Relationships: [
          {
            foreignKeyName: "agent_failures_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_modules: {
        Row: {
          available: boolean
          key: string
          label: string
        }
        Insert: {
          available?: boolean
          key: string
          label: string
        }
        Update: {
          available?: boolean
          key?: string
          label?: string
        }
        Relationships: []
      }
      agent_pool_placement_requests: {
        Row: {
          created_at: string
          error: string | null
          id: string
          org_id: string
          pool_id: string
          requested_by: string | null
          requested_by_email: string | null
          status: string
          worker_id: string
        }
        Insert: {
          created_at?: string
          error?: string | null
          id?: string
          org_id: string
          pool_id: string
          requested_by?: string | null
          requested_by_email?: string | null
          status?: string
          worker_id: string
        }
        Update: {
          created_at?: string
          error?: string | null
          id?: string
          org_id?: string
          pool_id?: string
          requested_by?: string | null
          requested_by_email?: string | null
          status?: string
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "agent_pool_placement_requests_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_pool_placement_requests_pool_id_fkey"
            columns: ["pool_id"]
            isOneToOne: false
            referencedRelation: "agent_servers"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_pool_placement_requests_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: true
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_presets: {
        Row: {
          archived_at: string | null
          created_at: string
          description: string
          escalates_to: string | null
          id: string
          is_default: boolean
          model: string | null
          name: string
          org_id: string
          seeded_version: number | null
          settings: Json
          sort_order: number
          template_key: string | null
          tool_grants: string[]
          updated_at: string
          version: number
        }
        Insert: {
          archived_at?: string | null
          created_at?: string
          description?: string
          escalates_to?: string | null
          id?: string
          is_default?: boolean
          model?: string | null
          name: string
          org_id: string
          seeded_version?: number | null
          settings?: Json
          sort_order?: number
          template_key?: string | null
          tool_grants?: string[]
          updated_at?: string
          version?: number
        }
        Update: {
          archived_at?: string | null
          created_at?: string
          description?: string
          escalates_to?: string | null
          id?: string
          is_default?: boolean
          model?: string | null
          name?: string
          org_id?: string
          seeded_version?: number | null
          settings?: Json
          sort_order?: number
          template_key?: string | null
          tool_grants?: string[]
          updated_at?: string
          version?: number
        }
        Relationships: [
          {
            foreignKeyName: "agent_presets_escalates_to_fkey"
            columns: ["escalates_to"]
            isOneToOne: false
            referencedRelation: "agent_presets"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_presets_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_server_jobs: {
        Row: {
          agent_server_id: string
          created_at: string
          error: string | null
          finished_at: string | null
          id: string
          kind: string
          log: string
          org_id: string
          slot_id: string | null
          started_at: string | null
          started_by: string | null
          started_by_email: string
          status: string
          step: string | null
          updated_at: string
        }
        Insert: {
          agent_server_id: string
          created_at?: string
          error?: string | null
          finished_at?: string | null
          id?: string
          kind: string
          log?: string
          org_id: string
          slot_id?: string | null
          started_at?: string | null
          started_by?: string | null
          started_by_email?: string
          status?: string
          step?: string | null
          updated_at?: string
        }
        Update: {
          agent_server_id?: string
          created_at?: string
          error?: string | null
          finished_at?: string | null
          id?: string
          kind?: string
          log?: string
          org_id?: string
          slot_id?: string | null
          started_at?: string | null
          started_by?: string | null
          started_by_email?: string
          status?: string
          step?: string | null
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "agent_server_jobs_agent_server_id_fkey"
            columns: ["agent_server_id"]
            isOneToOne: false
            referencedRelation: "agent_servers"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_server_jobs_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_server_jobs_slot_id_fkey"
            columns: ["slot_id"]
            isOneToOne: false
            referencedRelation: "agent_slots"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_servers: {
        Row: {
          agent_version: string | null
          allow_agent_sudo: boolean
          auto_repair_enabled: boolean
          bundle_hash: string | null
          capacity: number | null
          claude_connected_at: string | null
          cli_versions: Json
          cpu_count: number | null
          created_at: string
          disk_free_gb: number | null
          disk_total_gb: number | null
          extra_packages: string[]
          id: string
          last_probe_at: string | null
          load_avg: number | null
          mem_free_mb: number | null
          mem_total_mb: number | null
          modules: string[]
          org_id: string
          os_release: string | null
          pool_name: string | null
          probe_error: string | null
          provisioned_at: string | null
          server_id: string
          setup_commands: string
          shared: boolean
          slot_template: Json
          status: string
          updated_at: string
          workdir: string
          workspace_bytes: number | null
          workspace_count: number | null
        }
        Insert: {
          agent_version?: string | null
          allow_agent_sudo?: boolean
          auto_repair_enabled?: boolean
          bundle_hash?: string | null
          capacity?: number | null
          claude_connected_at?: string | null
          cli_versions?: Json
          cpu_count?: number | null
          created_at?: string
          disk_free_gb?: number | null
          disk_total_gb?: number | null
          extra_packages?: string[]
          id?: string
          last_probe_at?: string | null
          load_avg?: number | null
          mem_free_mb?: number | null
          mem_total_mb?: number | null
          modules?: string[]
          org_id: string
          os_release?: string | null
          pool_name?: string | null
          probe_error?: string | null
          provisioned_at?: string | null
          server_id: string
          setup_commands?: string
          shared?: boolean
          slot_template?: Json
          status?: string
          updated_at?: string
          workdir?: string
          workspace_bytes?: number | null
          workspace_count?: number | null
        }
        Update: {
          agent_version?: string | null
          allow_agent_sudo?: boolean
          auto_repair_enabled?: boolean
          bundle_hash?: string | null
          capacity?: number | null
          claude_connected_at?: string | null
          cli_versions?: Json
          cpu_count?: number | null
          created_at?: string
          disk_free_gb?: number | null
          disk_total_gb?: number | null
          extra_packages?: string[]
          id?: string
          last_probe_at?: string | null
          load_avg?: number | null
          mem_free_mb?: number | null
          mem_total_mb?: number | null
          modules?: string[]
          org_id?: string
          os_release?: string | null
          pool_name?: string | null
          probe_error?: string | null
          provisioned_at?: string | null
          server_id?: string
          setup_commands?: string
          shared?: boolean
          slot_template?: Json
          status?: string
          updated_at?: string
          workdir?: string
          workspace_bytes?: number | null
          workspace_count?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "agent_servers_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_servers_server_id_fkey"
            columns: ["server_id"]
            isOneToOne: true
            referencedRelation: "servers"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_session_events: {
        Row: {
          at: string
          content: string
          id: number
          kind: string
          org_id: string
          session_id: string
        }
        Insert: {
          at?: string
          content: string
          id?: number
          kind?: string
          org_id: string
          session_id: string
        }
        Update: {
          at?: string
          content?: string
          id?: number
          kind?: string
          org_id?: string
          session_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "agent_session_events_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_session_events_session_id_fkey"
            columns: ["session_id"]
            isOneToOne: false
            referencedRelation: "agent_sessions"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_sessions: {
        Row: {
          acp_session_id: string | null
          closed_at: string | null
          created_at: string
          created_by: string | null
          error: string | null
          id: string
          last_active_at: string
          org_id: string
          project_id: string
          status: string
          worker_id: string | null
          workspace_path: string | null
        }
        Insert: {
          acp_session_id?: string | null
          closed_at?: string | null
          created_at?: string
          created_by?: string | null
          error?: string | null
          id?: string
          last_active_at?: string
          org_id: string
          project_id: string
          status?: string
          worker_id?: string | null
          workspace_path?: string | null
        }
        Update: {
          acp_session_id?: string | null
          closed_at?: string | null
          created_at?: string
          created_by?: string | null
          error?: string | null
          id?: string
          last_active_at?: string
          org_id?: string
          project_id?: string
          status?: string
          worker_id?: string | null
          workspace_path?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "agent_sessions_created_by_fkey"
            columns: ["created_by"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_sessions_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_sessions_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_sessions_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      agent_slots: {
        Row: {
          agent_server_id: string
          agent_version: string | null
          auto_repair_attempts: number
          auto_repair_last_at: string | null
          auto_repair_needs_attention: boolean
          created_at: string
          desired_state: string
          id: string
          last_service_check: string | null
          name: string
          org_id: string
          principal_id: string | null
          service_name: string
          service_state: string | null
          slot_index: number
          status: string
          updated_at: string
          worker_id: string | null
          workspace_path: string
        }
        Insert: {
          agent_server_id: string
          agent_version?: string | null
          auto_repair_attempts?: number
          auto_repair_last_at?: string | null
          auto_repair_needs_attention?: boolean
          created_at?: string
          desired_state?: string
          id?: string
          last_service_check?: string | null
          name: string
          org_id: string
          principal_id?: string | null
          service_name: string
          service_state?: string | null
          slot_index: number
          status?: string
          updated_at?: string
          worker_id?: string | null
          workspace_path: string
        }
        Update: {
          agent_server_id?: string
          agent_version?: string | null
          auto_repair_attempts?: number
          auto_repair_last_at?: string | null
          auto_repair_needs_attention?: boolean
          created_at?: string
          desired_state?: string
          id?: string
          last_service_check?: string | null
          name?: string
          org_id?: string
          principal_id?: string | null
          service_name?: string
          service_state?: string | null
          slot_index?: number
          status?: string
          updated_at?: string
          worker_id?: string | null
          workspace_path?: string
        }
        Relationships: [
          {
            foreignKeyName: "agent_slots_agent_server_id_fkey"
            columns: ["agent_server_id"]
            isOneToOne: false
            referencedRelation: "agent_servers"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_slots_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_slots_principal_id_fkey"
            columns: ["principal_id"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "agent_slots_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      api_request_log: {
        Row: {
          created_at: string
          db_ms: number
          duration_ms: number
          id: number
          method: string
          route: string
          status_code: number
        }
        Insert: {
          created_at?: string
          db_ms: number
          duration_ms: number
          id?: number
          method: string
          route: string
          status_code: number
        }
        Update: {
          created_at?: string
          db_ms?: number
          duration_ms?: number
          id?: number
          method?: string
          route?: string
          status_code?: number
        }
        Relationships: []
      }
      app_issues: {
        Row: {
          context: Json
          created_at: string
          deployment_id: string
          fingerprint: string | null
          first_seen_at: string
          id: string
          last_seen_at: string
          message: string | null
          occurrence_count: number
          org_id: string
          project_id: string
          promoted_issue_id: string | null
          reporter_email: string | null
          reporter_name: string | null
          source: string
          stack_trace: string | null
          status: string
          title: string
          triaged_at: string | null
          triaged_by: string | null
          updated_at: string
        }
        Insert: {
          context?: Json
          created_at?: string
          deployment_id: string
          fingerprint?: string | null
          first_seen_at?: string
          id?: string
          last_seen_at?: string
          message?: string | null
          occurrence_count?: number
          org_id: string
          project_id: string
          promoted_issue_id?: string | null
          reporter_email?: string | null
          reporter_name?: string | null
          source: string
          stack_trace?: string | null
          status?: string
          title: string
          triaged_at?: string | null
          triaged_by?: string | null
          updated_at?: string
        }
        Update: {
          context?: Json
          created_at?: string
          deployment_id?: string
          fingerprint?: string | null
          first_seen_at?: string
          id?: string
          last_seen_at?: string
          message?: string | null
          occurrence_count?: number
          org_id?: string
          project_id?: string
          promoted_issue_id?: string | null
          reporter_email?: string | null
          reporter_name?: string | null
          source?: string
          stack_trace?: string | null
          status?: string
          title?: string
          triaged_at?: string | null
          triaged_by?: string | null
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "app_issues_deployment_id_org_id_fkey"
            columns: ["deployment_id", "org_id"]
            isOneToOne: false
            referencedRelation: "deployments"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "app_issues_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "app_issues_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "app_issues_promoted_issue_id_org_id_fkey"
            columns: ["promoted_issue_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "app_issues_triaged_by_fkey"
            columns: ["triaged_by"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
        ]
      }
      approvals: {
        Row: {
          actor: string | null
          auto_approved: boolean
          comment: string | null
          created_at: string
          decision: string
          gate: string
          id: string
          issue_id: string
          org_id: string
          payload: Json | null
          subject_id: string | null
          subject_type: string | null
        }
        Insert: {
          actor?: string | null
          auto_approved?: boolean
          comment?: string | null
          created_at?: string
          decision: string
          gate: string
          id?: string
          issue_id: string
          org_id: string
          payload?: Json | null
          subject_id?: string | null
          subject_type?: string | null
        }
        Update: {
          actor?: string | null
          auto_approved?: boolean
          comment?: string | null
          created_at?: string
          decision?: string
          gate?: string
          id?: string
          issue_id?: string
          org_id?: string
          payload?: Json | null
          subject_id?: string | null
          subject_type?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "approvals_issue_id_org_id_fkey"
            columns: ["issue_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "approvals_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      artifacts: {
        Row: {
          content: string
          created_at: string
          created_by: string
          id: string
          instruction_set: string | null
          issue_id: string
          kind: string
          org_id: string
          status: string
          updated_at: string
          version: number
        }
        Insert: {
          content?: string
          created_at?: string
          created_by: string
          id?: string
          instruction_set?: string | null
          issue_id: string
          kind: string
          org_id: string
          status?: string
          updated_at?: string
          version?: number
        }
        Update: {
          content?: string
          created_at?: string
          created_by?: string
          id?: string
          instruction_set?: string | null
          issue_id?: string
          kind?: string
          org_id?: string
          status?: string
          updated_at?: string
          version?: number
        }
        Relationships: [
          {
            foreignKeyName: "artifacts_issue_id_org_id_fkey"
            columns: ["issue_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "artifacts_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      clarifications: {
        Row: {
          answer: string | null
          answered_at: string | null
          answered_by: string | null
          asked_at: string
          id: string
          issue_id: string
          multi_select: boolean
          options: Json | null
          org_id: string
          question: string
          run_id: string
          selected_options: Json | null
          worker_id: string | null
        }
        Insert: {
          answer?: string | null
          answered_at?: string | null
          answered_by?: string | null
          asked_at?: string
          id?: string
          issue_id: string
          multi_select?: boolean
          options?: Json | null
          org_id: string
          question: string
          run_id: string
          selected_options?: Json | null
          worker_id?: string | null
        }
        Update: {
          answer?: string | null
          answered_at?: string | null
          answered_by?: string | null
          asked_at?: string
          id?: string
          issue_id?: string
          multi_select?: boolean
          options?: Json | null
          org_id?: string
          question?: string
          run_id?: string
          selected_options?: Json | null
          worker_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "clarifications_issue_id_org_id_fkey"
            columns: ["issue_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "clarifications_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "clarifications_run_id_fkey"
            columns: ["run_id"]
            isOneToOne: false
            referencedRelation: "runs"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "clarifications_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      claude_subscriptions: {
        Row: {
          created_at: string
          expires_at: string
          id: string
          key_last4: string
          org_id: string
          set_at: string
          updated_at: string
          vault_secret_id: string
        }
        Insert: {
          created_at?: string
          expires_at: string
          id?: string
          key_last4: string
          org_id: string
          set_at?: string
          updated_at?: string
          vault_secret_id: string
        }
        Update: {
          created_at?: string
          expires_at?: string
          id?: string
          key_last4?: string
          org_id?: string
          set_at?: string
          updated_at?: string
          vault_secret_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "claude_subscriptions_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: true
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      client_perf_events: {
        Row: {
          created_at: string
          id: number
          metric: string
          navigation_type: string | null
          org_id: string | null
          route: string
          user_id: string | null
          value: number
        }
        Insert: {
          created_at?: string
          id?: number
          metric: string
          navigation_type?: string | null
          org_id?: string | null
          route: string
          user_id?: string | null
          value: number
        }
        Update: {
          created_at?: string
          id?: number
          metric?: string
          navigation_type?: string | null
          org_id?: string | null
          route?: string
          user_id?: string | null
          value?: number
        }
        Relationships: [
          {
            foreignKeyName: "client_perf_events_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      content_audit: {
        Row: {
          action: string
          actor_id: string | null
          actor_name: string
          actor_type: string
          after_text: string | null
          before_text: string | null
          created_at: string
          id: string
          item_key: string
          org_id: string | null
          project_id: string
          surface: string
        }
        Insert: {
          action: string
          actor_id?: string | null
          actor_name?: string
          actor_type: string
          after_text?: string | null
          before_text?: string | null
          created_at?: string
          id?: string
          item_key?: string
          org_id?: string | null
          project_id: string
          surface: string
        }
        Update: {
          action?: string
          actor_id?: string | null
          actor_name?: string
          actor_type?: string
          after_text?: string | null
          before_text?: string | null
          created_at?: string
          id?: string
          item_key?: string
          org_id?: string | null
          project_id?: string
          surface?: string
        }
        Relationships: [
          {
            foreignKeyName: "content_audit_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      dashboard_incident_dismissals: {
        Row: {
          dismissed_at: string
          dismissed_by: string | null
          event_id: string
          id: number
          org_id: string
        }
        Insert: {
          dismissed_at?: string
          dismissed_by?: string | null
          event_id: string
          id?: never
          org_id: string
        }
        Update: {
          dismissed_at?: string
          dismissed_by?: string | null
          event_id?: string
          id?: never
          org_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "dashboard_incident_dismissals_event_id_fkey"
            columns: ["event_id"]
            isOneToOne: false
            referencedRelation: "issue_events"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "dashboard_incident_dismissals_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      deployment_env_vars: {
        Row: {
          created_at: string
          deployment_id: string
          name: string
          org_id: string
          updated_at: string
        }
        Insert: {
          created_at?: string
          deployment_id: string
          name: string
          org_id: string
          updated_at?: string
        }
        Update: {
          created_at?: string
          deployment_id?: string
          name?: string
          org_id?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "deployment_env_vars_deployment_id_org_id_fkey"
            columns: ["deployment_id", "org_id"]
            isOneToOne: false
            referencedRelation: "deployments"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "deployment_env_vars_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      deployment_events: {
        Row: {
          actor: string
          areas: Json
          created_at: string
          deployment_id: string
          detail: Json
          event: string
          id: number
          org_id: string
        }
        Insert: {
          actor?: string
          areas?: Json
          created_at?: string
          deployment_id: string
          detail?: Json
          event: string
          id?: never
          org_id: string
        }
        Update: {
          actor?: string
          areas?: Json
          created_at?: string
          deployment_id?: string
          detail?: Json
          event?: string
          id?: never
          org_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "deployment_events_deployment_id_org_id_fkey"
            columns: ["deployment_id", "org_id"]
            isOneToOne: false
            referencedRelation: "deployments"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "deployment_events_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      deployment_notifications: {
        Row: {
          created_at: string
          deployment_id: string
          events: Json
          org_id: string
          updated_at: string
        }
        Insert: {
          created_at?: string
          deployment_id: string
          events?: Json
          org_id: string
          updated_at?: string
        }
        Update: {
          created_at?: string
          deployment_id?: string
          events?: Json
          org_id?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "deployment_notifications_deployment_id_org_id_fkey"
            columns: ["deployment_id", "org_id"]
            isOneToOne: false
            referencedRelation: "deployments"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "deployment_notifications_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      deployment_run_events: {
        Row: {
          created_at: string
          data: Json
          id: number
          message: string
          org_id: string
          phase: string
          run_id: string
        }
        Insert: {
          created_at?: string
          data?: Json
          id?: never
          message?: string
          org_id: string
          phase: string
          run_id: string
        }
        Update: {
          created_at?: string
          data?: Json
          id?: never
          message?: string
          org_id?: string
          phase?: string
          run_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "deployment_run_events_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "deployment_run_events_run_id_org_id_fkey"
            columns: ["run_id", "org_id"]
            isOneToOne: false
            referencedRelation: "deployment_runs"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      deployment_runs: {
        Row: {
          artifact_bytes: number | null
          artifact_path: string | null
          artifact_sha256: string | null
          branch: string | null
          cancelled_by: string | null
          cancelled_by_email: string | null
          commit_message: string | null
          commit_sha: string | null
          created_at: string
          deployment_id: string
          finished_at: string | null
          id: string
          is_override: boolean
          kind: string
          log: string
          merge_commit_sha: string | null
          org_id: string
          pr_number: number | null
          promoted_from_run_id: string | null
          redeploy_of_run_id: string | null
          release_id: string | null
          release_path: string | null
          rollback_to_run_id: string | null
          source: string
          started_at: string | null
          started_by: string
          started_by_email: string
          status: string
          updated_at: string
          zip_filename: string | null
        }
        Insert: {
          artifact_bytes?: number | null
          artifact_path?: string | null
          artifact_sha256?: string | null
          branch?: string | null
          cancelled_by?: string | null
          cancelled_by_email?: string | null
          commit_message?: string | null
          commit_sha?: string | null
          created_at?: string
          deployment_id: string
          finished_at?: string | null
          id?: string
          is_override?: boolean
          kind?: string
          log?: string
          merge_commit_sha?: string | null
          org_id: string
          pr_number?: number | null
          promoted_from_run_id?: string | null
          redeploy_of_run_id?: string | null
          release_id?: string | null
          release_path?: string | null
          rollback_to_run_id?: string | null
          source?: string
          started_at?: string | null
          started_by: string
          started_by_email?: string
          status?: string
          updated_at?: string
          zip_filename?: string | null
        }
        Update: {
          artifact_bytes?: number | null
          artifact_path?: string | null
          artifact_sha256?: string | null
          branch?: string | null
          cancelled_by?: string | null
          cancelled_by_email?: string | null
          commit_message?: string | null
          commit_sha?: string | null
          created_at?: string
          deployment_id?: string
          finished_at?: string | null
          id?: string
          is_override?: boolean
          kind?: string
          log?: string
          merge_commit_sha?: string | null
          org_id?: string
          pr_number?: number | null
          promoted_from_run_id?: string | null
          redeploy_of_run_id?: string | null
          release_id?: string | null
          release_path?: string | null
          rollback_to_run_id?: string | null
          source?: string
          started_at?: string | null
          started_by?: string
          started_by_email?: string
          status?: string
          updated_at?: string
          zip_filename?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "deployment_runs_deployment_id_org_id_fkey"
            columns: ["deployment_id", "org_id"]
            isOneToOne: false
            referencedRelation: "deployments"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "deployment_runs_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "deployment_runs_release_id_fkey"
            columns: ["release_id"]
            isOneToOne: false
            referencedRelation: "releases"
            referencedColumns: ["id"]
          },
        ]
      }
      deployments: {
        Row: {
          agent_dispatch_allowed: boolean
          branch: string
          created_at: string
          current_run_id: string | null
          environment: string | null
          exclude_patterns: string
          health_check_expected_status: number
          health_check_initial_delay_seconds: number
          health_check_url: string
          health_check_window_seconds: number
          id: string
          is_self_monitoring: boolean
          issue_report_key_hash: string | null
          issue_report_key_last4: string | null
          issue_report_key_vault_secret_id: string | null
          issue_reporting_enabled: boolean
          keep_releases: number
          kind: string
          name: string
          org_id: string
          project_id: string
          protected: boolean
          run_timeout_minutes: number
          script: string
          server_id: string | null
          source_folder: string
          staged_zip_bytes: number | null
          staged_zip_filename: string | null
          staged_zip_sha256: string | null
          staged_zip_uploaded_at: string | null
          staged_zip_uploaded_by_email: string | null
          strategy: string
          target_branch: string
          target_folder: string | null
          updated_at: string
          website_kind: string | null
          website_url: string | null
        }
        Insert: {
          agent_dispatch_allowed?: boolean
          branch: string
          created_at?: string
          current_run_id?: string | null
          environment?: string | null
          exclude_patterns?: string
          health_check_expected_status?: number
          health_check_initial_delay_seconds?: number
          health_check_url?: string
          health_check_window_seconds?: number
          id?: string
          is_self_monitoring?: boolean
          issue_report_key_hash?: string | null
          issue_report_key_last4?: string | null
          issue_report_key_vault_secret_id?: string | null
          issue_reporting_enabled?: boolean
          keep_releases?: number
          kind?: string
          name: string
          org_id: string
          project_id: string
          protected?: boolean
          run_timeout_minutes?: number
          script?: string
          server_id?: string | null
          source_folder?: string
          staged_zip_bytes?: number | null
          staged_zip_filename?: string | null
          staged_zip_sha256?: string | null
          staged_zip_uploaded_at?: string | null
          staged_zip_uploaded_by_email?: string | null
          strategy?: string
          target_branch?: string
          target_folder?: string | null
          updated_at?: string
          website_kind?: string | null
          website_url?: string | null
        }
        Update: {
          agent_dispatch_allowed?: boolean
          branch?: string
          created_at?: string
          current_run_id?: string | null
          environment?: string | null
          exclude_patterns?: string
          health_check_expected_status?: number
          health_check_initial_delay_seconds?: number
          health_check_url?: string
          health_check_window_seconds?: number
          id?: string
          is_self_monitoring?: boolean
          issue_report_key_hash?: string | null
          issue_report_key_last4?: string | null
          issue_report_key_vault_secret_id?: string | null
          issue_reporting_enabled?: boolean
          keep_releases?: number
          kind?: string
          name?: string
          org_id?: string
          project_id?: string
          protected?: boolean
          run_timeout_minutes?: number
          script?: string
          server_id?: string | null
          source_folder?: string
          staged_zip_bytes?: number | null
          staged_zip_filename?: string | null
          staged_zip_sha256?: string | null
          staged_zip_uploaded_at?: string | null
          staged_zip_uploaded_by_email?: string | null
          strategy?: string
          target_branch?: string
          target_folder?: string | null
          updated_at?: string
          website_kind?: string | null
          website_url?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "deployments_current_run_id_fkey"
            columns: ["current_run_id"]
            isOneToOne: false
            referencedRelation: "deployment_runs"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "deployments_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "deployments_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "deployments_server_id_org_id_fkey"
            columns: ["server_id", "org_id"]
            isOneToOne: false
            referencedRelation: "servers"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      documents: {
        Row: {
          attached_to: string
          created_at: string
          created_by: string | null
          id: string
          issue_id: string | null
          mime_type: string
          name: string
          org_id: string
          project_id: string
          run_id: string | null
          size_bytes: number
          source: string
          storage_path: string
          test_case_id: string | null
          updated_at: string
        }
        Insert: {
          attached_to?: string
          created_at?: string
          created_by?: string | null
          id?: string
          issue_id?: string | null
          mime_type?: string
          name: string
          org_id: string
          project_id: string
          run_id?: string | null
          size_bytes?: number
          source?: string
          storage_path: string
          test_case_id?: string | null
          updated_at?: string
        }
        Update: {
          attached_to?: string
          created_at?: string
          created_by?: string | null
          id?: string
          issue_id?: string | null
          mime_type?: string
          name?: string
          org_id?: string
          project_id?: string
          run_id?: string | null
          size_bytes?: number
          source?: string
          storage_path?: string
          test_case_id?: string | null
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "documents_issue_id_org_id_fkey"
            columns: ["issue_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "documents_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "documents_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "documents_run_id_org_id_fkey"
            columns: ["run_id", "org_id"]
            isOneToOne: false
            referencedRelation: "runs"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "documents_test_case_org_fk"
            columns: ["test_case_id", "org_id"]
            isOneToOne: false
            referencedRelation: "test_cases"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      epics: {
        Row: {
          active: boolean
          completed_at: string | null
          completed_by: string | null
          created_at: string
          created_by: string | null
          description: string | null
          id: string
          number: number
          org_id: string
          project_id: string
          status: string
          title: string
          updated_at: string
        }
        Insert: {
          active?: boolean
          completed_at?: string | null
          completed_by?: string | null
          created_at?: string
          created_by?: string | null
          description?: string | null
          id?: string
          number: number
          org_id: string
          project_id: string
          status?: string
          title: string
          updated_at?: string
        }
        Update: {
          active?: boolean
          completed_at?: string | null
          completed_by?: string | null
          created_at?: string
          created_by?: string | null
          description?: string | null
          id?: string
          number?: number
          org_id?: string
          project_id?: string
          status?: string
          title?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "epics_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "epics_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      git_power_branch_heads: {
        Row: {
          branch: string
          head_sha: string
          principal_id: string
          project_id: string
          updated_at: string
        }
        Insert: {
          branch: string
          head_sha: string
          principal_id: string
          project_id: string
          updated_at?: string
        }
        Update: {
          branch?: string
          head_sha?: string
          principal_id?: string
          project_id?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "git_power_branch_heads_principal_id_fkey"
            columns: ["principal_id"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "git_power_branch_heads_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      git_power_grant_events: {
        Row: {
          actor: string
          created_at: string
          detail: Json
          event: string
          id: number
          org_id: string
          principal_id: string
          project_id: string
        }
        Insert: {
          actor?: string
          created_at?: string
          detail?: Json
          event: string
          id?: never
          org_id: string
          principal_id: string
          project_id: string
        }
        Update: {
          actor?: string
          created_at?: string
          detail?: Json
          event?: string
          id?: never
          org_id?: string
          principal_id?: string
          project_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "git_power_grant_events_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      git_power_grants: {
        Row: {
          allow_branch_delete: boolean
          allow_default_branch: boolean
          allow_force_push: boolean
          allow_tag_push: boolean
          created_at: string
          granted_by: string | null
          org_id: string
          principal_id: string
          project_id: string
          updated_at: string
        }
        Insert: {
          allow_branch_delete?: boolean
          allow_default_branch?: boolean
          allow_force_push?: boolean
          allow_tag_push?: boolean
          created_at?: string
          granted_by?: string | null
          org_id: string
          principal_id: string
          project_id: string
          updated_at?: string
        }
        Update: {
          allow_branch_delete?: boolean
          allow_default_branch?: boolean
          allow_force_push?: boolean
          allow_tag_push?: boolean
          created_at?: string
          granted_by?: string | null
          org_id?: string
          principal_id?: string
          project_id?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "git_power_grants_granted_by_fkey"
            columns: ["granted_by"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "git_power_grants_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "git_power_grants_principal_id_fkey"
            columns: ["principal_id"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "git_power_grants_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      github_connections: {
        Row: {
          account_login: string
          account_type: string
          connected_by: string
          created_at: string
          id: string
          installation_id: number | null
          method: string
          org_id: string
          pat_expires_at: string | null
          pat_last4: string | null
          repos: Json
          updated_at: string
          vault_secret_id: string | null
        }
        Insert: {
          account_login: string
          account_type: string
          connected_by: string
          created_at?: string
          id?: string
          installation_id?: number | null
          method: string
          org_id: string
          pat_expires_at?: string | null
          pat_last4?: string | null
          repos?: Json
          updated_at?: string
          vault_secret_id?: string | null
        }
        Update: {
          account_login?: string
          account_type?: string
          connected_by?: string
          created_at?: string
          id?: string
          installation_id?: number | null
          method?: string
          org_id?: string
          pat_expires_at?: string | null
          pat_last4?: string | null
          repos?: Json
          updated_at?: string
          vault_secret_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "github_connections_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      guideline_recommendations: {
        Row: {
          created_at: string
          decided_at: string | null
          decided_by: string | null
          decision_note: string | null
          id: string
          org_id: string
          project_id: string
          proposed_text: string
          rationale: string
          refresh_id: string | null
          section_id: string | null
          section_key: string
          section_title: string
          severity: string
          status: string
          worker_id: string | null
        }
        Insert: {
          created_at?: string
          decided_at?: string | null
          decided_by?: string | null
          decision_note?: string | null
          id?: string
          org_id: string
          project_id: string
          proposed_text: string
          rationale: string
          refresh_id?: string | null
          section_id?: string | null
          section_key?: string
          section_title?: string
          severity: string
          status?: string
          worker_id?: string | null
        }
        Update: {
          created_at?: string
          decided_at?: string | null
          decided_by?: string | null
          decision_note?: string | null
          id?: string
          org_id?: string
          project_id?: string
          proposed_text?: string
          rationale?: string
          refresh_id?: string | null
          section_id?: string | null
          section_key?: string
          section_title?: string
          severity?: string
          status?: string
          worker_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "guideline_recommendations_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "guideline_recommendations_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "guideline_recommendations_refresh_id_fkey"
            columns: ["refresh_id"]
            isOneToOne: false
            referencedRelation: "guideline_refreshes"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "guideline_recommendations_section_id_fkey"
            columns: ["section_id"]
            isOneToOne: false
            referencedRelation: "project_guidelines"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "guideline_recommendations_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      guideline_refreshes: {
        Row: {
          created_at: string
          decided_at: string | null
          focus: string
          id: string
          issue_id: string | null
          org_id: string
          project_id: string
          run_id: string | null
          scope: string
          status: string
          summary: string
          worker_id: string | null
        }
        Insert: {
          created_at?: string
          decided_at?: string | null
          focus?: string
          id?: string
          issue_id?: string | null
          org_id: string
          project_id: string
          run_id?: string | null
          scope?: string
          status?: string
          summary?: string
          worker_id?: string | null
        }
        Update: {
          created_at?: string
          decided_at?: string | null
          focus?: string
          id?: string
          issue_id?: string | null
          org_id?: string
          project_id?: string
          run_id?: string | null
          scope?: string
          status?: string
          summary?: string
          worker_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "guideline_refreshes_issue_id_fkey"
            columns: ["issue_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "guideline_refreshes_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "guideline_refreshes_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "guideline_refreshes_run_id_fkey"
            columns: ["run_id"]
            isOneToOne: false
            referencedRelation: "runs"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "guideline_refreshes_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      issue_comments: {
        Row: {
          author_kind: string
          author_user: string | null
          author_worker: string | null
          body: string
          created_at: string
          id: string
          issue_id: string
          org_id: string
          run_id: string | null
        }
        Insert: {
          author_kind: string
          author_user?: string | null
          author_worker?: string | null
          body: string
          created_at?: string
          id?: string
          issue_id: string
          org_id: string
          run_id?: string | null
        }
        Update: {
          author_kind?: string
          author_user?: string | null
          author_worker?: string | null
          body?: string
          created_at?: string
          id?: string
          issue_id?: string
          org_id?: string
          run_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "issue_comments_author_worker_fkey"
            columns: ["author_worker"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "issue_comments_issue_id_org_id_fkey"
            columns: ["issue_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "issue_comments_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "issue_comments_run_id_fkey"
            columns: ["run_id"]
            isOneToOne: false
            referencedRelation: "runs"
            referencedColumns: ["id"]
          },
        ]
      }
      issue_events: {
        Row: {
          created_at: string
          id: string
          issue_id: string
          org_id: string
          payload: Json
          type: string
        }
        Insert: {
          created_at?: string
          id?: string
          issue_id: string
          org_id: string
          payload?: Json
          type: string
        }
        Update: {
          created_at?: string
          id?: string
          issue_id?: string
          org_id?: string
          payload?: Json
          type?: string
        }
        Relationships: [
          {
            foreignKeyName: "issue_events_issue_id_org_id_fkey"
            columns: ["issue_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "issue_events_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      issues: {
        Row: {
          abandoned_at: string | null
          acceptance_criteria: Json
          assignee_id: string | null
          attempts_blocked_at: string | null
          body: string | null
          breakdown_instructions: string | null
          breakdown_mode: string
          complexity: string | null
          complexity_basis: string | null
          complexity_model: string | null
          complexity_rationale: string | null
          complexity_scored_at: string | null
          cost_usd: number
          created_at: string
          data_model_impact: string | null
          epic_id: string
          github_issue_number: number | null
          github_issue_url: string | null
          id: string
          instruction_set: string | null
          item_no: number | null
          merge_branches: string[]
          org_id: string
          parent_id: string | null
          project_id: string
          search_text: string | null
          status: string
          status_changed_at: string
          sub_no: number | null
          summary: string | null
          summary_generated_at: string | null
          summary_source_hash: string | null
          target_date: string | null
          title: string
          touches_critical: boolean | null
          type: string
          updated_at: string
        }
        Insert: {
          abandoned_at?: string | null
          acceptance_criteria?: Json
          assignee_id?: string | null
          attempts_blocked_at?: string | null
          body?: string | null
          breakdown_instructions?: string | null
          breakdown_mode?: string
          complexity?: string | null
          complexity_basis?: string | null
          complexity_model?: string | null
          complexity_rationale?: string | null
          complexity_scored_at?: string | null
          cost_usd?: number
          created_at?: string
          data_model_impact?: string | null
          epic_id: string
          github_issue_number?: number | null
          github_issue_url?: string | null
          id?: string
          instruction_set?: string | null
          item_no?: number | null
          merge_branches?: string[]
          org_id: string
          parent_id?: string | null
          project_id: string
          search_text?: string | null
          status?: string
          status_changed_at?: string
          sub_no?: number | null
          summary?: string | null
          summary_generated_at?: string | null
          summary_source_hash?: string | null
          target_date?: string | null
          title: string
          touches_critical?: boolean | null
          type?: string
          updated_at?: string
        }
        Update: {
          abandoned_at?: string | null
          acceptance_criteria?: Json
          assignee_id?: string | null
          attempts_blocked_at?: string | null
          body?: string | null
          breakdown_instructions?: string | null
          breakdown_mode?: string
          complexity?: string | null
          complexity_basis?: string | null
          complexity_model?: string | null
          complexity_rationale?: string | null
          complexity_scored_at?: string | null
          cost_usd?: number
          created_at?: string
          data_model_impact?: string | null
          epic_id?: string
          github_issue_number?: number | null
          github_issue_url?: string | null
          id?: string
          instruction_set?: string | null
          item_no?: number | null
          merge_branches?: string[]
          org_id?: string
          parent_id?: string | null
          project_id?: string
          search_text?: string | null
          status?: string
          status_changed_at?: string
          sub_no?: number | null
          summary?: string | null
          summary_generated_at?: string | null
          summary_source_hash?: string | null
          target_date?: string | null
          title?: string
          touches_critical?: boolean | null
          type?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "issues_assignee_id_fkey"
            columns: ["assignee_id"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "issues_epic_id_org_id_fkey"
            columns: ["epic_id", "org_id"]
            isOneToOne: false
            referencedRelation: "epics"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "issues_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "issues_parent_id_org_id_fkey"
            columns: ["parent_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "issues_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      learning_submissions: {
        Row: {
          created_at: string
          decided_at: string | null
          decided_by: string | null
          decision_note: string | null
          id: string
          org_id: string
          project_id: string
          status: string
          text: string
          worker_id: string | null
        }
        Insert: {
          created_at?: string
          decided_at?: string | null
          decided_by?: string | null
          decision_note?: string | null
          id?: string
          org_id: string
          project_id: string
          status?: string
          text: string
          worker_id?: string | null
        }
        Update: {
          created_at?: string
          decided_at?: string | null
          decided_by?: string | null
          decision_note?: string | null
          id?: string
          org_id?: string
          project_id?: string
          status?: string
          text?: string
          worker_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "learning_submissions_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "learning_submissions_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "learning_submissions_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      llm_function_routes: {
        Row: {
          created_at: string
          function_key: string
          id: string
          model: string
          org_id: string
          provider_id: string
          updated_at: string
        }
        Insert: {
          created_at?: string
          function_key: string
          id?: string
          model: string
          org_id: string
          provider_id: string
          updated_at?: string
        }
        Update: {
          created_at?: string
          function_key?: string
          id?: string
          model?: string
          org_id?: string
          provider_id?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "llm_function_routes_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "llm_function_routes_provider_id_fkey"
            columns: ["provider_id"]
            isOneToOne: false
            referencedRelation: "llm_providers"
            referencedColumns: ["id"]
          },
        ]
      }
      llm_gateway_keys: {
        Row: {
          created_at: string
          expires_at: string
          id: string
          key_hash: string
          model: string | null
          org_id: string
          platform_billed: boolean
          revoked_at: string | null
          route: string
          run_id: string | null
          session_id: string | null
          worker_id: string
        }
        Insert: {
          created_at?: string
          expires_at: string
          id?: string
          key_hash: string
          model?: string | null
          org_id: string
          platform_billed?: boolean
          revoked_at?: string | null
          route?: string
          run_id?: string | null
          session_id?: string | null
          worker_id: string
        }
        Update: {
          created_at?: string
          expires_at?: string
          id?: string
          key_hash?: string
          model?: string | null
          org_id?: string
          platform_billed?: boolean
          revoked_at?: string | null
          route?: string
          run_id?: string | null
          session_id?: string | null
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "llm_gateway_keys_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "llm_gateway_keys_session_id_fkey"
            columns: ["session_id"]
            isOneToOne: false
            referencedRelation: "agent_sessions"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "llm_gateway_keys_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      llm_model_prices: {
        Row: {
          cache_read_per_mtok: number | null
          cache_write_per_mtok: number | null
          created_at: string
          id: string
          input_per_mtok: number
          model: string
          org_id: string
          output_per_mtok: number
          updated_at: string
        }
        Insert: {
          cache_read_per_mtok?: number | null
          cache_write_per_mtok?: number | null
          created_at?: string
          id?: string
          input_per_mtok?: number
          model: string
          org_id: string
          output_per_mtok?: number
          updated_at?: string
        }
        Update: {
          cache_read_per_mtok?: number | null
          cache_write_per_mtok?: number | null
          created_at?: string
          id?: string
          input_per_mtok?: number
          model?: string
          org_id?: string
          output_per_mtok?: number
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "llm_model_prices_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      llm_prompt_templates: {
        Row: {
          content: string
          created_at: string
          id: string
          prompt_key: string
          updated_at: string
          updated_by: string | null
        }
        Insert: {
          content: string
          created_at?: string
          id?: string
          prompt_key: string
          updated_at?: string
          updated_by?: string | null
        }
        Update: {
          content?: string
          created_at?: string
          id?: string
          prompt_key?: string
          updated_at?: string
          updated_by?: string | null
        }
        Relationships: []
      }
      llm_providers: {
        Row: {
          base_url: string | null
          created_at: string
          default_model: string | null
          id: string
          is_default: boolean
          key_last4: string | null
          models: string[]
          name: string
          org_id: string
          provider_type: string
          updated_at: string
          vault_secret_id: string | null
        }
        Insert: {
          base_url?: string | null
          created_at?: string
          default_model?: string | null
          id?: string
          is_default?: boolean
          key_last4?: string | null
          models?: string[]
          name: string
          org_id: string
          provider_type: string
          updated_at?: string
          vault_secret_id?: string | null
        }
        Update: {
          base_url?: string | null
          created_at?: string
          default_model?: string | null
          id?: string
          is_default?: boolean
          key_last4?: string | null
          models?: string[]
          name?: string
          org_id?: string
          provider_type?: string
          updated_at?: string
          vault_secret_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "llm_settings_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      llm_usage: {
        Row: {
          cache_read_tokens: number | null
          cache_write_tokens: number | null
          cost_usd: number | null
          created_at: string
          id: number
          latency_ms: number | null
          model: string
          org_id: string
          parse_note: string | null
          parsed: boolean
          project_id: string | null
          provider_id: string | null
          provider_name: string
          provider_type: string
          rate_in_per_mtok: number | null
          rate_out_per_mtok: number | null
          route: string
          run_id: string | null
          session_id: string | null
          status_code: number | null
          tokens_in: number | null
          tokens_out: number | null
          worker_id: string | null
        }
        Insert: {
          cache_read_tokens?: number | null
          cache_write_tokens?: number | null
          cost_usd?: number | null
          created_at?: string
          id?: number
          latency_ms?: number | null
          model?: string
          org_id: string
          parse_note?: string | null
          parsed?: boolean
          project_id?: string | null
          provider_id?: string | null
          provider_name?: string
          provider_type?: string
          rate_in_per_mtok?: number | null
          rate_out_per_mtok?: number | null
          route?: string
          run_id?: string | null
          session_id?: string | null
          status_code?: number | null
          tokens_in?: number | null
          tokens_out?: number | null
          worker_id?: string | null
        }
        Update: {
          cache_read_tokens?: number | null
          cache_write_tokens?: number | null
          cost_usd?: number | null
          created_at?: string
          id?: number
          latency_ms?: number | null
          model?: string
          org_id?: string
          parse_note?: string | null
          parsed?: boolean
          project_id?: string | null
          provider_id?: string | null
          provider_name?: string
          provider_type?: string
          rate_in_per_mtok?: number | null
          rate_out_per_mtok?: number | null
          route?: string
          run_id?: string | null
          session_id?: string | null
          status_code?: number | null
          tokens_in?: number | null
          tokens_out?: number | null
          worker_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "llm_usage_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "llm_usage_session_id_fkey"
            columns: ["session_id"]
            isOneToOne: false
            referencedRelation: "agent_sessions"
            referencedColumns: ["id"]
          },
        ]
      }
      mcp_scoped_keys: {
        Row: {
          created_at: string
          expires_at: string
          id: string
          key_hash: string
          org_id: string
          revoked_at: string | null
          run_id: string
          worker_id: string
        }
        Insert: {
          created_at?: string
          expires_at: string
          id?: string
          key_hash: string
          org_id: string
          revoked_at?: string | null
          run_id: string
          worker_id: string
        }
        Update: {
          created_at?: string
          expires_at?: string
          id?: string
          key_hash?: string
          org_id?: string
          revoked_at?: string | null
          run_id?: string
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "mcp_scoped_keys_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "mcp_scoped_keys_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      mcp_servers: {
        Row: {
          command: string | null
          created_at: string
          credential_header: string | null
          declared_tools: string[]
          description: string
          enabled: boolean
          endpoint: string | null
          id: string
          key_last4: string | null
          last_check_error: string | null
          last_check_ok: boolean | null
          last_checked_at: string | null
          name: string
          needs_credential: boolean
          org_id: string
          slug: string
          transport: string
          updated_at: string
          vault_secret_id: string | null
        }
        Insert: {
          command?: string | null
          created_at?: string
          credential_header?: string | null
          declared_tools?: string[]
          description?: string
          enabled?: boolean
          endpoint?: string | null
          id?: string
          key_last4?: string | null
          last_check_error?: string | null
          last_check_ok?: boolean | null
          last_checked_at?: string | null
          name: string
          needs_credential?: boolean
          org_id: string
          slug: string
          transport: string
          updated_at?: string
          vault_secret_id?: string | null
        }
        Update: {
          command?: string | null
          created_at?: string
          credential_header?: string | null
          declared_tools?: string[]
          description?: string
          enabled?: boolean
          endpoint?: string | null
          id?: string
          key_last4?: string | null
          last_check_error?: string | null
          last_check_ok?: boolean | null
          last_checked_at?: string | null
          name?: string
          needs_credential?: boolean
          org_id?: string
          slug?: string
          transport?: string
          updated_at?: string
          vault_secret_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "mcp_servers_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      mcp_tool_calls: {
        Row: {
          arguments_redacted: Json | null
          created_at: string
          duration_ms: number | null
          error: string | null
          id: number
          org_id: string
          outcome: string
          response_bytes: number | null
          run_id: string | null
          server_id: string | null
          server_name: string
          tool: string
          worker_id: string | null
        }
        Insert: {
          arguments_redacted?: Json | null
          created_at?: string
          duration_ms?: number | null
          error?: string | null
          id?: number
          org_id: string
          outcome?: string
          response_bytes?: number | null
          run_id?: string | null
          server_id?: string | null
          server_name?: string
          tool?: string
          worker_id?: string | null
        }
        Update: {
          arguments_redacted?: Json | null
          created_at?: string
          duration_ms?: number | null
          error?: string | null
          id?: number
          org_id?: string
          outcome?: string
          response_bytes?: number | null
          run_id?: string | null
          server_id?: string | null
          server_name?: string
          tool?: string
          worker_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "mcp_tool_calls_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      notification_endpoints: {
        Row: {
          created_at: string
          format: string
          id: string
          last_delivery_at: string | null
          last_delivery_error: string | null
          last_delivery_ok: boolean | null
          name: string
          org_id: string
          updated_at: string
          url_host: string
        }
        Insert: {
          created_at?: string
          format?: string
          id?: string
          last_delivery_at?: string | null
          last_delivery_error?: string | null
          last_delivery_ok?: boolean | null
          name: string
          org_id: string
          updated_at?: string
          url_host?: string
        }
        Update: {
          created_at?: string
          format?: string
          id?: string
          last_delivery_at?: string | null
          last_delivery_error?: string | null
          last_delivery_ok?: boolean | null
          name?: string
          org_id?: string
          updated_at?: string
          url_host?: string
        }
        Relationships: [
          {
            foreignKeyName: "notification_endpoints_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      notifications: {
        Row: {
          created_at: string
          id: string
          org_id: string
          payload: Json
          read_at: string | null
          recipient_id: string
          type: string
        }
        Insert: {
          created_at?: string
          id?: string
          org_id: string
          payload?: Json
          read_at?: string | null
          recipient_id: string
          type: string
        }
        Update: {
          created_at?: string
          id?: string
          org_id?: string
          payload?: Json
          read_at?: string | null
          recipient_id?: string
          type?: string
        }
        Relationships: [
          {
            foreignKeyName: "notifications_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "notifications_recipient_id_fkey"
            columns: ["recipient_id"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
        ]
      }
      org_project_template_sections: {
        Row: {
          content: string
          created_at: string
          id: string
          org_id: string
          org_template_id: string
          section_key: string
          section_type: string
          sort_order: number
          title: string
          updated_at: string
        }
        Insert: {
          content?: string
          created_at?: string
          id?: string
          org_id: string
          org_template_id: string
          section_key: string
          section_type: string
          sort_order?: number
          title?: string
          updated_at?: string
        }
        Update: {
          content?: string
          created_at?: string
          id?: string
          org_id?: string
          org_template_id?: string
          section_key?: string
          section_type?: string
          sort_order?: number
          title?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "org_project_template_sections_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "org_project_template_sections_org_template_id_fkey"
            columns: ["org_template_id"]
            isOneToOne: false
            referencedRelation: "org_project_templates"
            referencedColumns: ["id"]
          },
        ]
      }
      org_project_templates: {
        Row: {
          archived_at: string | null
          created_at: string
          description: string
          id: string
          is_available: boolean
          is_default: boolean
          name: string
          org_id: string
          seeded_version: number | null
          sort_order: number
          template_key: string | null
          updated_at: string
        }
        Insert: {
          archived_at?: string | null
          created_at?: string
          description?: string
          id?: string
          is_available?: boolean
          is_default?: boolean
          name: string
          org_id: string
          seeded_version?: number | null
          sort_order?: number
          template_key?: string | null
          updated_at?: string
        }
        Update: {
          archived_at?: string | null
          created_at?: string
          description?: string
          id?: string
          is_available?: boolean
          is_default?: boolean
          name?: string
          org_id?: string
          seeded_version?: number | null
          sort_order?: number
          template_key?: string | null
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "org_project_templates_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      organization_members: {
        Row: {
          created_at: string
          org_id: string
          principal_id: string
          role: string
          status: string
          user_id: string | null
        }
        Insert: {
          created_at?: string
          org_id: string
          principal_id: string
          role?: string
          status?: string
          user_id?: string | null
        }
        Update: {
          created_at?: string
          org_id?: string
          principal_id?: string
          role?: string
          status?: string
          user_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "organization_members_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "organization_members_principal_id_fkey"
            columns: ["principal_id"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "organization_members_user_id_profiles_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      organizations: {
        Row: {
          archived_at: string | null
          created_at: string
          id: string
          is_platform_admin: boolean
          max_agents: number
          max_item_attempts: number
          name: string
          shortname: string
        }
        Insert: {
          archived_at?: string | null
          created_at?: string
          id?: string
          is_platform_admin?: boolean
          max_agents?: number
          max_item_attempts?: number
          name: string
          shortname: string
        }
        Update: {
          archived_at?: string | null
          created_at?: string
          id?: string
          is_platform_admin?: boolean
          max_agents?: number
          max_item_attempts?: number
          name?: string
          shortname?: string
        }
        Relationships: []
      }
      platform_llm_key: {
        Row: {
          id: boolean
          key_last4: string | null
          model: string
          updated_at: string
          vault_secret_id: string | null
        }
        Insert: {
          id?: boolean
          key_last4?: string | null
          model?: string
          updated_at?: string
          vault_secret_id?: string | null
        }
        Update: {
          id?: boolean
          key_last4?: string | null
          model?: string
          updated_at?: string
          vault_secret_id?: string | null
        }
        Relationships: []
      }
      platform_run_config: {
        Row: {
          autonomy_policy: Json
          id: boolean
          max_item_attempts: number
          max_run_minutes: number | null
          max_total_run_minutes: number | null
          model_routes: Json
          run_routes: Json
          updated_at: string
        }
        Insert: {
          autonomy_policy?: Json
          id?: boolean
          max_item_attempts?: number
          max_run_minutes?: number | null
          max_total_run_minutes?: number | null
          model_routes?: Json
          run_routes?: Json
          updated_at?: string
        }
        Update: {
          autonomy_policy?: Json
          id?: boolean
          max_item_attempts?: number
          max_run_minutes?: number | null
          max_total_run_minutes?: number | null
          model_routes?: Json
          run_routes?: Json
          updated_at?: string
        }
        Relationships: []
      }
      preset_templates: {
        Row: {
          created_at: string
          description: string
          id: string
          key: string
          model_hint: string
          name: string
          settings: Json
          sort_order: number
          updated_at: string
          updated_by: string | null
          version: number
        }
        Insert: {
          created_at?: string
          description?: string
          id?: string
          key: string
          model_hint?: string
          name: string
          settings?: Json
          sort_order?: number
          updated_at?: string
          updated_by?: string | null
          version?: number
        }
        Update: {
          created_at?: string
          description?: string
          id?: string
          key?: string
          model_hint?: string
          name?: string
          settings?: Json
          sort_order?: number
          updated_at?: string
          updated_by?: string | null
          version?: number
        }
        Relationships: []
      }
      principals: {
        Row: {
          active_org_id: string | null
          auth_user_id: string | null
          avatar_url: string | null
          created_at: string
          display_name: string | null
          email: string | null
          id: string
          kind: string
          must_change_password: boolean
        }
        Insert: {
          active_org_id?: string | null
          auth_user_id?: string | null
          avatar_url?: string | null
          created_at?: string
          display_name?: string | null
          email?: string | null
          id?: string
          kind: string
          must_change_password?: boolean
        }
        Update: {
          active_org_id?: string | null
          auth_user_id?: string | null
          avatar_url?: string | null
          created_at?: string
          display_name?: string | null
          email?: string | null
          id?: string
          kind?: string
          must_change_password?: boolean
        }
        Relationships: [
          {
            foreignKeyName: "principals_active_org_id_fkey"
            columns: ["active_org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      profiles: {
        Row: {
          approved_at: string | null
          approved_by: string | null
          avatar_url: string | null
          created_at: string
          display_name: string | null
          email: string
          id: string
        }
        Insert: {
          approved_at?: string | null
          approved_by?: string | null
          avatar_url?: string | null
          created_at?: string
          display_name?: string | null
          email: string
          id: string
        }
        Update: {
          approved_at?: string | null
          approved_by?: string | null
          avatar_url?: string | null
          created_at?: string
          display_name?: string | null
          email?: string
          id?: string
        }
        Relationships: []
      }
      project_build_config: {
        Row: {
          created_at: string
          id: string
          name: string
          org_id: string
          project_id: string
          updated_at: string
          updated_by: string | null
        }
        Insert: {
          created_at?: string
          id?: string
          name: string
          org_id: string
          project_id: string
          updated_at?: string
          updated_by?: string | null
        }
        Update: {
          created_at?: string
          id?: string
          name?: string
          org_id?: string
          project_id?: string
          updated_at?: string
          updated_by?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "project_build_config_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "project_build_config_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      project_env: {
        Row: {
          agent_id: string | null
          created_at: string
          description: string
          fingerprint: string | null
          id: string
          kind: string
          name: string
          org_id: string
          project_id: string
          updated_at: string
          updated_by: string | null
          value: string | null
        }
        Insert: {
          agent_id?: string | null
          created_at?: string
          description?: string
          fingerprint?: string | null
          id?: string
          kind?: string
          name: string
          org_id: string
          project_id: string
          updated_at?: string
          updated_by?: string | null
          value?: string | null
        }
        Update: {
          agent_id?: string | null
          created_at?: string
          description?: string
          fingerprint?: string | null
          id?: string
          kind?: string
          name?: string
          org_id?: string
          project_id?: string
          updated_at?: string
          updated_by?: string | null
          value?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "project_env_agent_id_fkey"
            columns: ["agent_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "project_env_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "project_env_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      project_guidelines: {
        Row: {
          content: string
          created_at: string
          id: string
          org_id: string
          project_id: string
          section_key: string
          sort_order: number
          title: string
          updated_at: string
        }
        Insert: {
          content?: string
          created_at?: string
          id?: string
          org_id: string
          project_id: string
          section_key?: string
          sort_order?: number
          title: string
          updated_at?: string
        }
        Update: {
          content?: string
          created_at?: string
          id?: string
          org_id?: string
          project_id?: string
          section_key?: string
          sort_order?: number
          title?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "project_guidelines_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "project_guidelines_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      project_learnings: {
        Row: {
          content: string
          created_at: string
          id: string
          last_updated_by: string
          org_id: string
          project_id: string
          updated_at: string
        }
        Insert: {
          content?: string
          created_at?: string
          id?: string
          last_updated_by?: string
          org_id: string
          project_id: string
          updated_at?: string
        }
        Update: {
          content?: string
          created_at?: string
          id?: string
          last_updated_by?: string
          org_id?: string
          project_id?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "project_learnings_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "project_learnings_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: true
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      project_modules: {
        Row: {
          created_at: string
          id: string
          name: string
          org_id: string
          path_globs: Json
          project_id: string
          updated_at: string
        }
        Insert: {
          created_at?: string
          id?: string
          name: string
          org_id: string
          path_globs?: Json
          project_id: string
          updated_at?: string
        }
        Update: {
          created_at?: string
          id?: string
          name?: string
          org_id?: string
          path_globs?: Json
          project_id?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "project_modules_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "project_modules_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      project_template_sections: {
        Row: {
          content: string
          created_at: string
          id: string
          section_key: string
          section_type: string
          sort_order: number
          template_id: string
          title: string
          updated_at: string
        }
        Insert: {
          content?: string
          created_at?: string
          id?: string
          section_key: string
          section_type: string
          sort_order?: number
          template_id: string
          title?: string
          updated_at?: string
        }
        Update: {
          content?: string
          created_at?: string
          id?: string
          section_key?: string
          section_type?: string
          sort_order?: number
          template_id?: string
          title?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "project_template_sections_template_id_fkey"
            columns: ["template_id"]
            isOneToOne: false
            referencedRelation: "project_templates"
            referencedColumns: ["id"]
          },
        ]
      }
      project_templates: {
        Row: {
          category: string
          created_at: string
          description: string
          id: string
          is_default: boolean
          is_disabled: boolean
          key: string
          name: string
          sort_order: number
          updated_at: string
          updated_by: string | null
          version: number
        }
        Insert: {
          category?: string
          created_at?: string
          description?: string
          id?: string
          is_default?: boolean
          is_disabled?: boolean
          key: string
          name: string
          sort_order?: number
          updated_at?: string
          updated_by?: string | null
          version?: number
        }
        Update: {
          category?: string
          created_at?: string
          description?: string
          id?: string
          is_default?: boolean
          is_disabled?: boolean
          key?: string
          name?: string
          sort_order?: number
          updated_at?: string
          updated_by?: string | null
          version?: number
        }
        Relationships: []
      }
      projects: {
        Row: {
          agent_instructions: string
          archived_at: string | null
          auto_approve_code: boolean
          auto_approve_plan: boolean
          auto_approve_prd: boolean
          budget_enabled: boolean
          budget_started_at: string | null
          budget_usd: number | null
          build_mode: string
          created_at: string
          default_branch: string
          description: string | null
          dev_branch_strategy: string
          docs_tree_enabled: boolean
          env_notes: string
          env_runtime: string
          env_setup_commands: Json
          follow_build_order: boolean
          guidelines_ready_at: string | null
          guidelines_ready_by: string | null
          id: string
          instructions_synced_at: string | null
          instructions_synced_hash: string | null
          instructions_synced_sha: string | null
          mcp_withheld: string[]
          name: string
          org_id: string
          org_template_id: string | null
          presubmit_test_command: string | null
          production_branch: string | null
          release_prod_deployment_id: string | null
          release_uat_deployment_id: string | null
          repo_full_name: string
          route_feature_as_one: boolean
          sequential_only: boolean
          slug: string
          summary: string | null
          uat_branch: string | null
          updated_at: string
          worker_instructions_ready_at: string | null
          worker_instructions_ready_by: string | null
        }
        Insert: {
          agent_instructions?: string
          archived_at?: string | null
          auto_approve_code?: boolean
          auto_approve_plan?: boolean
          auto_approve_prd?: boolean
          budget_enabled?: boolean
          budget_started_at?: string | null
          budget_usd?: number | null
          build_mode?: string
          created_at?: string
          default_branch?: string
          description?: string | null
          dev_branch_strategy?: string
          docs_tree_enabled?: boolean
          env_notes?: string
          env_runtime?: string
          env_setup_commands?: Json
          follow_build_order?: boolean
          guidelines_ready_at?: string | null
          guidelines_ready_by?: string | null
          id?: string
          instructions_synced_at?: string | null
          instructions_synced_hash?: string | null
          instructions_synced_sha?: string | null
          mcp_withheld?: string[]
          name: string
          org_id: string
          org_template_id?: string | null
          presubmit_test_command?: string | null
          production_branch?: string | null
          release_prod_deployment_id?: string | null
          release_uat_deployment_id?: string | null
          repo_full_name: string
          route_feature_as_one?: boolean
          sequential_only?: boolean
          slug: string
          summary?: string | null
          uat_branch?: string | null
          updated_at?: string
          worker_instructions_ready_at?: string | null
          worker_instructions_ready_by?: string | null
        }
        Update: {
          agent_instructions?: string
          archived_at?: string | null
          auto_approve_code?: boolean
          auto_approve_plan?: boolean
          auto_approve_prd?: boolean
          budget_enabled?: boolean
          budget_started_at?: string | null
          budget_usd?: number | null
          build_mode?: string
          created_at?: string
          default_branch?: string
          description?: string | null
          dev_branch_strategy?: string
          docs_tree_enabled?: boolean
          env_notes?: string
          env_runtime?: string
          env_setup_commands?: Json
          follow_build_order?: boolean
          guidelines_ready_at?: string | null
          guidelines_ready_by?: string | null
          id?: string
          instructions_synced_at?: string | null
          instructions_synced_hash?: string | null
          instructions_synced_sha?: string | null
          mcp_withheld?: string[]
          name?: string
          org_id?: string
          org_template_id?: string | null
          presubmit_test_command?: string | null
          production_branch?: string | null
          release_prod_deployment_id?: string | null
          release_uat_deployment_id?: string | null
          repo_full_name?: string
          route_feature_as_one?: boolean
          sequential_only?: boolean
          slug?: string
          summary?: string | null
          uat_branch?: string | null
          updated_at?: string
          worker_instructions_ready_at?: string | null
          worker_instructions_ready_by?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "projects_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "projects_org_template_id_fkey"
            columns: ["org_template_id"]
            isOneToOne: false
            referencedRelation: "org_project_templates"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "projects_release_prod_deployment_id_fkey"
            columns: ["release_prod_deployment_id"]
            isOneToOne: false
            referencedRelation: "deployments"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "projects_release_uat_deployment_id_fkey"
            columns: ["release_uat_deployment_id"]
            isOneToOne: false
            referencedRelation: "deployments"
            referencedColumns: ["id"]
          },
        ]
      }
      release_prep_runs: {
        Row: {
          claim_expires_at: string | null
          claimed_at: string | null
          created_at: string
          error: string | null
          finished_at: string | null
          id: string
          notes_detail: string | null
          notes_summary: string | null
          org_id: string
          project_id: string
          release_id: string
          requested_by: string | null
          status: string
          updated_at: string
          worker_id: string | null
        }
        Insert: {
          claim_expires_at?: string | null
          claimed_at?: string | null
          created_at?: string
          error?: string | null
          finished_at?: string | null
          id?: string
          notes_detail?: string | null
          notes_summary?: string | null
          org_id: string
          project_id: string
          release_id: string
          requested_by?: string | null
          status?: string
          updated_at?: string
          worker_id?: string | null
        }
        Update: {
          claim_expires_at?: string | null
          claimed_at?: string | null
          created_at?: string
          error?: string | null
          finished_at?: string | null
          id?: string
          notes_detail?: string | null
          notes_summary?: string | null
          org_id?: string
          project_id?: string
          release_id?: string
          requested_by?: string | null
          status?: string
          updated_at?: string
          worker_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "release_prep_runs_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "release_prep_runs_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "release_prep_runs_release_id_fkey"
            columns: ["release_id"]
            isOneToOne: false
            referencedRelation: "releases"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "release_prep_runs_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      release_test_results: {
        Row: {
          comment: string | null
          id: string
          noted_at: string
          noted_by: string | null
          org_id: string
          release_id: string
          result: string
          suite_run_id: string | null
          test_case_id: string
        }
        Insert: {
          comment?: string | null
          id?: string
          noted_at?: string
          noted_by?: string | null
          org_id: string
          release_id: string
          result: string
          suite_run_id?: string | null
          test_case_id: string
        }
        Update: {
          comment?: string | null
          id?: string
          noted_at?: string
          noted_by?: string | null
          org_id?: string
          release_id?: string
          result?: string
          suite_run_id?: string | null
          test_case_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "release_test_results_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "release_test_results_release_id_fkey"
            columns: ["release_id"]
            isOneToOne: false
            referencedRelation: "releases"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "release_test_results_suite_run_id_fkey"
            columns: ["suite_run_id"]
            isOneToOne: false
            referencedRelation: "suite_runs"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "release_test_results_test_case_id_fkey"
            columns: ["test_case_id"]
            isOneToOne: false
            referencedRelation: "test_cases"
            referencedColumns: ["id"]
          },
        ]
      }
      releases: {
        Row: {
          cancelled_at: string | null
          cancelled_by: string | null
          cases_attached_at: string | null
          commit_sha: string
          created_at: string
          created_by: string | null
          failure_reason: string | null
          git_tag: string | null
          id: string
          included_items: Json
          notes_detail: string | null
          notes_summary: string | null
          notes_written_at: string | null
          org_id: string
          previous_release_id: string | null
          prod_deployment_run_id: string | null
          project_id: string
          promoted_at: string | null
          promoted_by: string | null
          rejected_at: string | null
          rejected_reason: string | null
          released_at: string | null
          rolled_back_at: string | null
          signed_off_at: string | null
          signed_off_by: string | null
          status: string
          touched_modules: Json
          uat_deployed_at: string | null
          uat_deployment_run_id: string | null
          updated_at: string
          version: string
        }
        Insert: {
          cancelled_at?: string | null
          cancelled_by?: string | null
          cases_attached_at?: string | null
          commit_sha: string
          created_at?: string
          created_by?: string | null
          failure_reason?: string | null
          git_tag?: string | null
          id?: string
          included_items?: Json
          notes_detail?: string | null
          notes_summary?: string | null
          notes_written_at?: string | null
          org_id: string
          previous_release_id?: string | null
          prod_deployment_run_id?: string | null
          project_id: string
          promoted_at?: string | null
          promoted_by?: string | null
          rejected_at?: string | null
          rejected_reason?: string | null
          released_at?: string | null
          rolled_back_at?: string | null
          signed_off_at?: string | null
          signed_off_by?: string | null
          status?: string
          touched_modules?: Json
          uat_deployed_at?: string | null
          uat_deployment_run_id?: string | null
          updated_at?: string
          version: string
        }
        Update: {
          cancelled_at?: string | null
          cancelled_by?: string | null
          cases_attached_at?: string | null
          commit_sha?: string
          created_at?: string
          created_by?: string | null
          failure_reason?: string | null
          git_tag?: string | null
          id?: string
          included_items?: Json
          notes_detail?: string | null
          notes_summary?: string | null
          notes_written_at?: string | null
          org_id?: string
          previous_release_id?: string | null
          prod_deployment_run_id?: string | null
          project_id?: string
          promoted_at?: string | null
          promoted_by?: string | null
          rejected_at?: string | null
          rejected_reason?: string | null
          released_at?: string | null
          rolled_back_at?: string | null
          signed_off_at?: string | null
          signed_off_by?: string | null
          status?: string
          touched_modules?: Json
          uat_deployed_at?: string | null
          uat_deployment_run_id?: string | null
          updated_at?: string
          version?: string
        }
        Relationships: [
          {
            foreignKeyName: "releases_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "releases_previous_release_id_fkey"
            columns: ["previous_release_id"]
            isOneToOne: false
            referencedRelation: "releases"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "releases_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      role_capabilities: {
        Row: {
          allowed: boolean
          capability: string
          role: string
        }
        Insert: {
          allowed?: boolean
          capability: string
          role: string
        }
        Update: {
          allowed?: boolean
          capability?: string
          role?: string
        }
        Relationships: []
      }
      run_activity: {
        Row: {
          at: string
          id: number
          org_id: string
          run_id: string
          tool: string
        }
        Insert: {
          at?: string
          id?: never
          org_id: string
          run_id: string
          tool: string
        }
        Update: {
          at?: string
          id?: never
          org_id?: string
          run_id?: string
          tool?: string
        }
        Relationships: [
          {
            foreignKeyName: "run_activity_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "run_activity_run_id_fkey"
            columns: ["run_id"]
            isOneToOne: false
            referencedRelation: "runs"
            referencedColumns: ["id"]
          },
        ]
      }
      run_attempts: {
        Row: {
          created_at: string
          id: number
          issue_id: string
          kind: string
          org_id: string
          reason: string
          run_id: string | null
          worker_id: string | null
        }
        Insert: {
          created_at?: string
          id?: never
          issue_id: string
          kind: string
          org_id: string
          reason: string
          run_id?: string | null
          worker_id?: string | null
        }
        Update: {
          created_at?: string
          id?: never
          issue_id?: string
          kind?: string
          org_id?: string
          reason?: string
          run_id?: string | null
          worker_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "run_attempts_issue_id_org_id_fkey"
            columns: ["issue_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "run_attempts_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      run_item_commits: {
        Row: {
          commit_sha: string
          created_at: string
          files_changed: number | null
          id: string
          issue_id: string
          message: string
          org_id: string
          run_id: string
        }
        Insert: {
          commit_sha: string
          created_at?: string
          files_changed?: number | null
          id?: string
          issue_id: string
          message?: string
          org_id: string
          run_id: string
        }
        Update: {
          commit_sha?: string
          created_at?: string
          files_changed?: number | null
          id?: string
          issue_id?: string
          message?: string
          org_id?: string
          run_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "run_item_commits_issue_id_fkey"
            columns: ["issue_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "run_item_commits_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "run_item_commits_run_id_fkey"
            columns: ["run_id"]
            isOneToOne: false
            referencedRelation: "runs"
            referencedColumns: ["id"]
          },
        ]
      }
      run_items: {
        Row: {
          created_at: string
          issue_id: string
          org_id: string
          position: number
          prev_issue_status: string | null
          run_id: string
        }
        Insert: {
          created_at?: string
          issue_id: string
          org_id: string
          position: number
          prev_issue_status?: string | null
          run_id: string
        }
        Update: {
          created_at?: string
          issue_id?: string
          org_id?: string
          position?: number
          prev_issue_status?: string | null
          run_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "run_items_issue_id_fkey"
            columns: ["issue_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "run_items_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "run_items_run_id_fkey"
            columns: ["run_id"]
            isOneToOne: false
            referencedRelation: "runs"
            referencedColumns: ["id"]
          },
        ]
      }
      run_trace: {
        Row: {
          at: string
          content: string
          id: number
          issue_id: string | null
          kind: string
          org_id: string
          principal_id: string | null
          run_id: string
        }
        Insert: {
          at?: string
          content: string
          id?: never
          issue_id?: string | null
          kind: string
          org_id: string
          principal_id?: string | null
          run_id: string
        }
        Update: {
          at?: string
          content?: string
          id?: never
          issue_id?: string | null
          kind?: string
          org_id?: string
          principal_id?: string | null
          run_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "run_trace_issue_id_fkey"
            columns: ["issue_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "run_trace_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "run_trace_run_id_fkey"
            columns: ["run_id"]
            isOneToOne: false
            referencedRelation: "runs"
            referencedColumns: ["id"]
          },
        ]
      }
      runner_command_audit: {
        Row: {
          argv: string[]
          cwd: string | null
          exit_code: number | null
          finished_at: string | null
          id: string
          org_id: string
          output: string | null
          policy_decision: string
          run_id: string | null
          session_id: string | null
          started_at: string
          worker_id: string
        }
        Insert: {
          argv?: string[]
          cwd?: string | null
          exit_code?: number | null
          finished_at?: string | null
          id?: string
          org_id: string
          output?: string | null
          policy_decision?: string
          run_id?: string | null
          session_id?: string | null
          started_at?: string
          worker_id: string
        }
        Update: {
          argv?: string[]
          cwd?: string | null
          exit_code?: number | null
          finished_at?: string | null
          id?: string
          org_id?: string
          output?: string | null
          policy_decision?: string
          run_id?: string | null
          session_id?: string | null
          started_at?: string
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "runner_command_audit_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "runner_command_audit_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      runner_config: {
        Row: {
          autonomy_policy: Json
          claude_billing: string
          enabled_kinds: Json | null
          enabled_modules: string[]
          max_item_attempts: number
          max_run_minutes: number | null
          max_total_run_minutes: number | null
          model_overrides: Json
          model_routes: Json
          org_id: string
          paused: boolean
          run_routes: Json
          updated_at: string
          worker_id: string
        }
        Insert: {
          autonomy_policy?: Json
          claude_billing?: string
          enabled_kinds?: Json | null
          enabled_modules?: string[]
          max_item_attempts?: number
          max_run_minutes?: number | null
          max_total_run_minutes?: number | null
          model_overrides?: Json
          model_routes?: Json
          org_id: string
          paused?: boolean
          run_routes?: Json
          updated_at?: string
          worker_id: string
        }
        Update: {
          autonomy_policy?: Json
          claude_billing?: string
          enabled_kinds?: Json | null
          enabled_modules?: string[]
          max_item_attempts?: number
          max_run_minutes?: number | null
          max_total_run_minutes?: number | null
          model_overrides?: Json
          model_routes?: Json
          org_id?: string
          paused?: boolean
          run_routes?: Json
          updated_at?: string
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "runner_config_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "runner_config_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: true
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      runner_incidents: {
        Row: {
          created_at: string
          id: string
          kind: string
          message: string | null
          org_id: string
          run_id: string | null
          worker_id: string
        }
        Insert: {
          created_at?: string
          id?: string
          kind?: string
          message?: string | null
          org_id: string
          run_id?: string | null
          worker_id: string
        }
        Update: {
          created_at?: string
          id?: string
          kind?: string
          message?: string | null
          org_id?: string
          run_id?: string | null
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "runner_incidents_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "runner_incidents_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      runner_sessions: {
        Row: {
          agent_versions: Json
          connected_at: string
          disconnected_at: string | null
          host_info: Json
          id: string
          last_seen_at: string
          module_settings: Json
          modules_available: string[]
          org_id: string
          worker_id: string
        }
        Insert: {
          agent_versions?: Json
          connected_at?: string
          disconnected_at?: string | null
          host_info?: Json
          id?: string
          last_seen_at?: string
          module_settings?: Json
          modules_available?: string[]
          org_id: string
          worker_id: string
        }
        Update: {
          agent_versions?: Json
          connected_at?: string
          disconnected_at?: string | null
          host_info?: Json
          id?: string
          last_seen_at?: string
          module_settings?: Json
          modules_available?: string[]
          org_id?: string
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "runner_sessions_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "runner_sessions_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      runs: {
        Row: {
          abandon_reason: string | null
          abandoned_at: string | null
          abandoned_by: string | null
          billing: string
          branch_ref: string | null
          cancel_reason: string | null
          cancelled_at: string | null
          cancelled_by: string | null
          change_breakdown: Json | null
          claim_expires_at: string | null
          claimed_at: string | null
          clarification_rounds: number
          claude_session_id: string | null
          cost_usd: number | null
          created_at: string
          deployment_id: string | null
          diff: string | null
          error: string | null
          fault_class: string | null
          files_changed: number | null
          finished_at: string | null
          handback_notes: string | null
          id: string
          input_context: Json
          issue_id: string | null
          kind: string
          last_heartbeat_at: string | null
          lines_added: number | null
          lines_removed: number | null
          merge_commit_sha: string | null
          merged_unapproved_at: string | null
          org_id: string
          paused_at: string | null
          pr_url: string | null
          preset_id: string | null
          preset_name: string | null
          preset_version: number | null
          prev_issue_status: string | null
          project_id: string
          provider: string
          pushed_at: string | null
          pushed_head_sha: string | null
          queue_rank: number | null
          resolved_settings: Json | null
          resume_attempts: number
          resume_reason: string | null
          resume_state_at: string | null
          reviewer_id: string | null
          settings_sources: Json | null
          spec_map: Json | null
          started_at: string | null
          status: string
          stdout: string | null
          stop_requested_at: string | null
          stopped_reason: string | null
          test_evidence: Json | null
          tokens_in: number | null
          tokens_out: number | null
          tool_calls_dropped: number
          tool_surface: Json | null
          updated_at: string
          work_seconds: number | null
          worker_id: string | null
        }
        Insert: {
          abandon_reason?: string | null
          abandoned_at?: string | null
          abandoned_by?: string | null
          billing?: string
          branch_ref?: string | null
          cancel_reason?: string | null
          cancelled_at?: string | null
          cancelled_by?: string | null
          change_breakdown?: Json | null
          claim_expires_at?: string | null
          claimed_at?: string | null
          clarification_rounds?: number
          claude_session_id?: string | null
          cost_usd?: number | null
          created_at?: string
          deployment_id?: string | null
          diff?: string | null
          error?: string | null
          fault_class?: string | null
          files_changed?: number | null
          finished_at?: string | null
          handback_notes?: string | null
          id?: string
          input_context: Json
          issue_id?: string | null
          kind?: string
          last_heartbeat_at?: string | null
          lines_added?: number | null
          lines_removed?: number | null
          merge_commit_sha?: string | null
          merged_unapproved_at?: string | null
          org_id: string
          paused_at?: string | null
          pr_url?: string | null
          preset_id?: string | null
          preset_name?: string | null
          preset_version?: number | null
          prev_issue_status?: string | null
          project_id: string
          provider?: string
          pushed_at?: string | null
          pushed_head_sha?: string | null
          queue_rank?: number | null
          resolved_settings?: Json | null
          resume_attempts?: number
          resume_reason?: string | null
          resume_state_at?: string | null
          reviewer_id?: string | null
          settings_sources?: Json | null
          spec_map?: Json | null
          started_at?: string | null
          status?: string
          stdout?: string | null
          stop_requested_at?: string | null
          stopped_reason?: string | null
          test_evidence?: Json | null
          tokens_in?: number | null
          tokens_out?: number | null
          tool_calls_dropped?: number
          tool_surface?: Json | null
          updated_at?: string
          work_seconds?: number | null
          worker_id?: string | null
        }
        Update: {
          abandon_reason?: string | null
          abandoned_at?: string | null
          abandoned_by?: string | null
          billing?: string
          branch_ref?: string | null
          cancel_reason?: string | null
          cancelled_at?: string | null
          cancelled_by?: string | null
          change_breakdown?: Json | null
          claim_expires_at?: string | null
          claimed_at?: string | null
          clarification_rounds?: number
          claude_session_id?: string | null
          cost_usd?: number | null
          created_at?: string
          deployment_id?: string | null
          diff?: string | null
          error?: string | null
          fault_class?: string | null
          files_changed?: number | null
          finished_at?: string | null
          handback_notes?: string | null
          id?: string
          input_context?: Json
          issue_id?: string | null
          kind?: string
          last_heartbeat_at?: string | null
          lines_added?: number | null
          lines_removed?: number | null
          merge_commit_sha?: string | null
          merged_unapproved_at?: string | null
          org_id?: string
          paused_at?: string | null
          pr_url?: string | null
          preset_id?: string | null
          preset_name?: string | null
          preset_version?: number | null
          prev_issue_status?: string | null
          project_id?: string
          provider?: string
          pushed_at?: string | null
          pushed_head_sha?: string | null
          queue_rank?: number | null
          resolved_settings?: Json | null
          resume_attempts?: number
          resume_reason?: string | null
          resume_state_at?: string | null
          reviewer_id?: string | null
          settings_sources?: Json | null
          spec_map?: Json | null
          started_at?: string | null
          status?: string
          stdout?: string | null
          stop_requested_at?: string | null
          stopped_reason?: string | null
          test_evidence?: Json | null
          tokens_in?: number | null
          tokens_out?: number | null
          tool_calls_dropped?: number
          tool_surface?: Json | null
          updated_at?: string
          work_seconds?: number | null
          worker_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "runs_deployment_fk"
            columns: ["deployment_id", "org_id"]
            isOneToOne: false
            referencedRelation: "deployments"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "runs_issue_org_fk"
            columns: ["issue_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "runs_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "runs_preset_id_fkey"
            columns: ["preset_id"]
            isOneToOne: false
            referencedRelation: "agent_presets"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "runs_project_fk"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "runs_reviewer_id_fkey"
            columns: ["reviewer_id"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "runs_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      servers: {
        Row: {
          auth_method: string
          created_at: string
          host: string
          host_key_fingerprint: string | null
          id: string
          key_fingerprint: string | null
          name: string
          org_id: string
          port: number
          updated_at: string
          username: string
        }
        Insert: {
          auth_method: string
          created_at?: string
          host: string
          host_key_fingerprint?: string | null
          id?: string
          key_fingerprint?: string | null
          name: string
          org_id: string
          port?: number
          updated_at?: string
          username: string
        }
        Update: {
          auth_method?: string
          created_at?: string
          host?: string
          host_key_fingerprint?: string | null
          id?: string
          key_fingerprint?: string | null
          name?: string
          org_id?: string
          port?: number
          updated_at?: string
          username?: string
        }
        Relationships: [
          {
            foreignKeyName: "servers_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      suite_run_events: {
        Row: {
          created_at: string
          data: Json
          id: number
          message: string
          org_id: string
          phase: string
          run_id: string
        }
        Insert: {
          created_at?: string
          data?: Json
          id?: never
          message?: string
          org_id: string
          phase: string
          run_id: string
        }
        Update: {
          created_at?: string
          data?: Json
          id?: never
          message?: string
          org_id?: string
          phase?: string
          run_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "suite_run_events_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "suite_run_events_run_id_org_id_fkey"
            columns: ["run_id", "org_id"]
            isOneToOne: false
            referencedRelation: "suite_runs"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      suite_run_tests: {
        Row: {
          created_at: string
          duration_ms: number | null
          id: string
          message: string | null
          org_id: string
          spec_ref: string
          status: string
          suite_run_id: string
          test_case_id: string | null
        }
        Insert: {
          created_at?: string
          duration_ms?: number | null
          id?: string
          message?: string | null
          org_id: string
          spec_ref: string
          status: string
          suite_run_id: string
          test_case_id?: string | null
        }
        Update: {
          created_at?: string
          duration_ms?: number | null
          id?: string
          message?: string | null
          org_id?: string
          spec_ref?: string
          status?: string
          suite_run_id?: string
          test_case_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "suite_run_tests_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "suite_run_tests_suite_run_id_org_id_fkey"
            columns: ["suite_run_id", "org_id"]
            isOneToOne: false
            referencedRelation: "suite_runs"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "suite_run_tests_test_case_id_fkey"
            columns: ["test_case_id"]
            isOneToOne: false
            referencedRelation: "test_cases"
            referencedColumns: ["id"]
          },
        ]
      }
      suite_runs: {
        Row: {
          base_url: string
          commit_sha: string
          created_at: string
          deployment_id: string
          error: string | null
          finished_at: string | null
          id: string
          log: string
          org_id: string
          project_id: string
          release_id: string | null
          started_at: string | null
          status: string
          suite_id: string
          tests_failed: number | null
          tests_passed: number | null
          tests_skipped: number | null
          tests_total: number | null
          trigger: string
          updated_at: string
          waive_reason: string | null
          waived_at: string | null
          waived_by: string | null
        }
        Insert: {
          base_url: string
          commit_sha: string
          created_at?: string
          deployment_id: string
          error?: string | null
          finished_at?: string | null
          id?: string
          log?: string
          org_id: string
          project_id: string
          release_id?: string | null
          started_at?: string | null
          status?: string
          suite_id: string
          tests_failed?: number | null
          tests_passed?: number | null
          tests_skipped?: number | null
          tests_total?: number | null
          trigger: string
          updated_at?: string
          waive_reason?: string | null
          waived_at?: string | null
          waived_by?: string | null
        }
        Update: {
          base_url?: string
          commit_sha?: string
          created_at?: string
          deployment_id?: string
          error?: string | null
          finished_at?: string | null
          id?: string
          log?: string
          org_id?: string
          project_id?: string
          release_id?: string | null
          started_at?: string | null
          status?: string
          suite_id?: string
          tests_failed?: number | null
          tests_passed?: number | null
          tests_skipped?: number | null
          tests_total?: number | null
          trigger?: string
          updated_at?: string
          waive_reason?: string | null
          waived_at?: string | null
          waived_by?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "suite_runs_deployment_id_org_id_fkey"
            columns: ["deployment_id", "org_id"]
            isOneToOne: false
            referencedRelation: "deployments"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "suite_runs_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "suite_runs_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "suite_runs_release_id_fkey"
            columns: ["release_id"]
            isOneToOne: false
            referencedRelation: "releases"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "suite_runs_suite_id_org_id_fkey"
            columns: ["suite_id", "org_id"]
            isOneToOne: false
            referencedRelation: "test_suites"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      test_cases: {
        Row: {
          always_on_uat: boolean
          created_at: string
          environments: Json
          execution: string
          expected_result: string
          id: string
          issue_id: string | null
          module_id: string | null
          org_id: string
          project_id: string
          release_id: string | null
          source: string
          spec_ref: string | null
          status: string
          steps: string
          suite_id: string | null
          test_types: Json
          title: string
          updated_at: string
        }
        Insert: {
          always_on_uat?: boolean
          created_at?: string
          environments?: Json
          execution?: string
          expected_result?: string
          id?: string
          issue_id?: string | null
          module_id?: string | null
          org_id: string
          project_id: string
          release_id?: string | null
          source?: string
          spec_ref?: string | null
          status?: string
          steps?: string
          suite_id?: string | null
          test_types?: Json
          title: string
          updated_at?: string
        }
        Update: {
          always_on_uat?: boolean
          created_at?: string
          environments?: Json
          execution?: string
          expected_result?: string
          id?: string
          issue_id?: string | null
          module_id?: string | null
          org_id?: string
          project_id?: string
          release_id?: string | null
          source?: string
          spec_ref?: string | null
          status?: string
          steps?: string
          suite_id?: string | null
          test_types?: Json
          title?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "test_cases_issue_org_fk"
            columns: ["issue_id", "org_id"]
            isOneToOne: false
            referencedRelation: "issues"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "test_cases_module_fk"
            columns: ["module_id", "org_id"]
            isOneToOne: false
            referencedRelation: "project_modules"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "test_cases_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "test_cases_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "test_cases_release_id_fkey"
            columns: ["release_id"]
            isOneToOne: false
            referencedRelation: "releases"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "test_cases_suite_fk"
            columns: ["suite_id", "org_id"]
            isOneToOne: false
            referencedRelation: "test_suites"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      test_run_results: {
        Row: {
          id: string
          note: string | null
          org_id: string
          recorded_at: string | null
          result: string
          test_case_id: string
          test_run_id: string
        }
        Insert: {
          id?: string
          note?: string | null
          org_id: string
          recorded_at?: string | null
          result?: string
          test_case_id: string
          test_run_id: string
        }
        Update: {
          id?: string
          note?: string | null
          org_id?: string
          recorded_at?: string | null
          result?: string
          test_case_id?: string
          test_run_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "test_run_results_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "test_run_results_test_case_id_fkey"
            columns: ["test_case_id"]
            isOneToOne: false
            referencedRelation: "test_cases"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "test_run_results_test_run_id_fkey"
            columns: ["test_run_id"]
            isOneToOne: false
            referencedRelation: "test_runs"
            referencedColumns: ["id"]
          },
        ]
      }
      test_runs: {
        Row: {
          completed_at: string | null
          created_at: string
          environment: string
          id: string
          label: string
          org_id: string
          project_id: string
          run_id: string | null
          source: string
          started_by: string | null
          status: string
          worker_id: string | null
          worker_name: string
        }
        Insert: {
          completed_at?: string | null
          created_at?: string
          environment: string
          id?: string
          label?: string
          org_id: string
          project_id: string
          run_id?: string | null
          source?: string
          started_by?: string | null
          status?: string
          worker_id?: string | null
          worker_name?: string
        }
        Update: {
          completed_at?: string | null
          created_at?: string
          environment?: string
          id?: string
          label?: string
          org_id?: string
          project_id?: string
          run_id?: string | null
          source?: string
          started_by?: string | null
          status?: string
          worker_id?: string | null
          worker_name?: string
        }
        Relationships: [
          {
            foreignKeyName: "test_runs_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "test_runs_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "test_runs_run_id_fkey"
            columns: ["run_id"]
            isOneToOne: false
            referencedRelation: "runs"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "test_runs_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      test_suites: {
        Row: {
          blocks_signoff: boolean
          created_at: string
          id: string
          layer: string
          name: string
          org_id: string
          project_id: string
          results_path: string
          run_command: string
          run_on_prod: boolean
          run_on_uat: boolean
          server_id: string | null
          status: string
          timeout_minutes: number
          updated_at: string
        }
        Insert: {
          blocks_signoff?: boolean
          created_at?: string
          id?: string
          layer: string
          name: string
          org_id: string
          project_id: string
          results_path?: string
          run_command?: string
          run_on_prod?: boolean
          run_on_uat?: boolean
          server_id?: string | null
          status?: string
          timeout_minutes?: number
          updated_at?: string
        }
        Update: {
          blocks_signoff?: boolean
          created_at?: string
          id?: string
          layer?: string
          name?: string
          org_id?: string
          project_id?: string
          results_path?: string
          run_command?: string
          run_on_prod?: boolean
          run_on_uat?: boolean
          server_id?: string | null
          status?: string
          timeout_minutes?: number
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "test_suites_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "test_suites_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "test_suites_server_id_org_id_fkey"
            columns: ["server_id", "org_id"]
            isOneToOne: false
            referencedRelation: "servers"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      user_activity_sessions: {
        Row: {
          active_ms: number
          created_at: string
          ended_at: string
          id: number
          issue_id: string | null
          kind: string
          org_id: string
          started_at: string
          user_id: string
        }
        Insert: {
          active_ms: number
          created_at?: string
          ended_at: string
          id?: number
          issue_id?: string | null
          kind: string
          org_id: string
          started_at: string
          user_id: string
        }
        Update: {
          active_ms?: number
          created_at?: string
          ended_at?: string
          id?: number
          issue_id?: string | null
          kind?: string
          org_id?: string
          started_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "user_activity_sessions_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      worker_capabilities: {
        Row: {
          capability: string
          created_at: string
          id: string
          org_id: string
          project_id: string
          worker_id: string
        }
        Insert: {
          capability: string
          created_at?: string
          id?: string
          org_id: string
          project_id: string
          worker_id: string
        }
        Update: {
          capability?: string
          created_at?: string
          id?: string
          org_id?: string
          project_id?: string
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "worker_capabilities_v2_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "worker_capabilities_v2_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "worker_capabilities_v2_worker_id_org_id_fkey"
            columns: ["worker_id", "org_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      worker_capability_events: {
        Row: {
          actor: string
          created_at: string
          detail: Json
          event: string
          id: number
          org_id: string
          worker_id: string
        }
        Insert: {
          actor?: string
          created_at?: string
          detail?: Json
          event: string
          id?: never
          org_id: string
          worker_id: string
        }
        Update: {
          actor?: string
          created_at?: string
          detail?: Json
          event?: string
          id?: never
          org_id?: string
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "worker_capability_events_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "worker_capability_events_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      worker_instructions: {
        Row: {
          content: string
          created_at: string
          id: string
          org_id: string
          project_id: string
          run_kind: string
          updated_at: string
          updated_by: string | null
        }
        Insert: {
          content?: string
          created_at?: string
          id?: string
          org_id: string
          project_id: string
          run_kind: string
          updated_at?: string
          updated_by?: string | null
        }
        Update: {
          content?: string
          created_at?: string
          id?: string
          org_id?: string
          project_id?: string
          run_kind?: string
          updated_at?: string
          updated_by?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "worker_instructions_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "worker_instructions_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
        ]
      }
      workers: {
        Row: {
          created_at: string
          id: string
          last_seen_at: string | null
          name: string
          no_claim_checkout: boolean
          org_id: string
          principal_id: string | null
          project_id: string | null
          revoked_by_suspension_at: string | null
          status: string
          token_hash: string
          token_last4: string
          type: string
          user_id: string | null
          vault_secret_id: string | null
        }
        Insert: {
          created_at?: string
          id?: string
          last_seen_at?: string | null
          name: string
          no_claim_checkout?: boolean
          org_id: string
          principal_id?: string | null
          project_id?: string | null
          revoked_by_suspension_at?: string | null
          status?: string
          token_hash: string
          token_last4: string
          type: string
          user_id?: string | null
          vault_secret_id?: string | null
        }
        Update: {
          created_at?: string
          id?: string
          last_seen_at?: string | null
          name?: string
          no_claim_checkout?: boolean
          org_id?: string
          principal_id?: string | null
          project_id?: string | null
          revoked_by_suspension_at?: string | null
          status?: string
          token_hash?: string
          token_last4?: string
          type?: string
          user_id?: string | null
          vault_secret_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "workers_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "workers_principal_id_fkey"
            columns: ["principal_id"]
            isOneToOne: false
            referencedRelation: "principals"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "workers_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      workspace_deliveries: {
        Row: {
          base_sha: string
          org_id: string
          paths: string[]
          project_id: string
          served_at: string
          worker_id: string
        }
        Insert: {
          base_sha: string
          org_id: string
          paths?: string[]
          project_id: string
          served_at?: string
          worker_id: string
        }
        Update: {
          base_sha?: string
          org_id?: string
          paths?: string[]
          project_id?: string
          served_at?: string
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "workspace_deliveries_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "workspace_deliveries_project_id_org_id_fkey"
            columns: ["project_id", "org_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id", "org_id"]
          },
          {
            foreignKeyName: "workspace_deliveries_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
      workspace_prep_jobs: {
        Row: {
          created_at: string
          error: string | null
          finished_at: string | null
          id: string
          org_id: string
          prepared_commit: string | null
          project_id: string
          started_at: string | null
          started_by: string | null
          started_by_email: string | null
          status: string
          steps: Json
          updated_at: string
          workdir: string | null
          worker_id: string
        }
        Insert: {
          created_at?: string
          error?: string | null
          finished_at?: string | null
          id?: string
          org_id: string
          prepared_commit?: string | null
          project_id: string
          started_at?: string | null
          started_by?: string | null
          started_by_email?: string | null
          status?: string
          steps?: Json
          updated_at?: string
          workdir?: string | null
          worker_id: string
        }
        Update: {
          created_at?: string
          error?: string | null
          finished_at?: string | null
          id?: string
          org_id?: string
          prepared_commit?: string | null
          project_id?: string
          started_at?: string | null
          started_by?: string | null
          started_by_email?: string | null
          status?: string
          steps?: Json
          updated_at?: string
          workdir?: string | null
          worker_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "workspace_prep_jobs_org_id_fkey"
            columns: ["org_id"]
            isOneToOne: false
            referencedRelation: "organizations"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "workspace_prep_jobs_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "workspace_prep_jobs_worker_id_fkey"
            columns: ["worker_id"]
            isOneToOne: false
            referencedRelation: "workers"
            referencedColumns: ["id"]
          },
        ]
      }
    }
    Views: {
      activity_feed: {
        Row: {
          action: string | null
          actor_id: string | null
          actor_name: string | null
          actor_type: string | null
          created_at: string | null
          detail: Json | null
          id: string | null
          kind: string | null
          object_id: string | null
          object_label: string | null
          object_type: string | null
          org_id: string | null
          outcome: string | null
          project_id: string | null
          project_name: string | null
        }
        Relationships: []
      }
    }
    Functions: {
      add_org_member_by_email: {
        Args: { p_email: string; p_org: string }
        Returns: undefined
      }
      admin_force_delete_org: { Args: { p_org_id: string }; Returns: undefined }
      agent_failure_run_context: { Args: { p_failure: number }; Returns: Json }
      approve_run: { Args: { p_run: string }; Returns: undefined }
      approve_run_precheck: { Args: { p_run: string }; Returns: string }
      assemble_project_guidelines: {
        Args: { p_project: string }
        Returns: string
      }
      assemble_project_learnings: {
        Args: { p_project: string }
        Returns: string
      }
      attach_release_inherited_cases: {
        Args: { p_release: string }
        Returns: number
      }
      auto_approve_code: { Args: { p_run: string }; Returns: undefined }
      auto_approve_plan: { Args: { p_issue: string }; Returns: string }
      auto_approve_prd: { Args: { p_issue: string }; Returns: string }
      available_agent_pools: {
        Args: never
        Returns: {
          free_slots: number
          pool_id: string
          pool_name: string
          status: string
        }[]
      }
      baked_guideline_section: { Args: { p_key: string }; Returns: string }
      baked_system_issue_fix_prompt: { Args: never; Returns: string }
      baked_worker_instruction: { Args: { p_kind: string }; Returns: string }
      build_issue_instructions: {
        Args: { p_issue: string; p_kind: string }
        Returns: string
      }
      clear_claude_subscription_token: {
        Args: { p_org: string }
        Returns: undefined
      }
      clear_llm_provider_key: {
        Args: { p_provider: string }
        Returns: undefined
      }
      clear_platform_llm_key: { Args: never; Returns: undefined }
      connect_github_pat: {
        Args: {
          p_account_login: string
          p_account_type: string
          p_expires_at: string
          p_org: string
          p_repos: Json
          p_token: string
        }
        Returns: string
      }
      content_audit_actor: { Args: never; Returns: Record<string, unknown> }
      copy_project_template_into_org: {
        Args: { p_name: string; p_org: string; p_template_id: string }
        Returns: string
      }
      count_dropped_tool_call: { Args: { p_run: string }; Returns: undefined }
      create_worker: {
        Args: {
          p_name: string
          p_org: string
          p_project?: string
          p_type: string
          p_user_id?: string
        }
        Returns: {
          token: string
          worker_id: string
        }[]
      }
      curate_feature_stories: { Args: { p_feature: string }; Returns: number }
      decide_guideline_recommendation: {
        Args: { p_accept: boolean; p_note?: string; p_recommendation: string }
        Returns: Json
      }
      default_buildmill_workflow_section: { Args: never; Returns: string }
      default_guidelines_release_section: { Args: never; Returns: string }
      default_worker_instruction: { Args: { p_kind: string }; Returns: string }
      delete_github_connection: { Args: { p_id: string }; Returns: undefined }
      dispatch_breakdown: { Args: { p_issue: string }; Returns: string }
      dispatch_elaboration: { Args: { p_issue: string }; Returns: string }
      dispatch_feature_batch: { Args: { p_feature: string }; Returns: Json }
      dispatch_issue: {
        Args: { p_issue: string; p_kind?: string }
        Returns: string
      }
      dispatch_kind_for: {
        Args: { p_issue: string; p_kind?: string }
        Returns: string
      }
      dispatch_merge: {
        Args: { p_branch_heads: Json; p_issue: string }
        Returns: string
      }
      dispatch_prd_draft: { Args: { p_issue: string }; Returns: string }
      dispatch_wireframe: {
        Args: { p_feedback?: string; p_issue: string }
        Returns: string
      }
      dispatch_wireframe_batch: { Args: { p_feature: string }; Returns: Json }
      effective_guideline_section: { Args: { p_key: string }; Returns: string }
      effective_system_issue_fix_prompt: { Args: never; Returns: string }
      feature_dispatch_phase: { Args: { p_feature: string }; Returns: Json }
      force_delete_issues: { Args: { p_issue_ids: string[] }; Returns: number }
      generate_deployment_report_key: {
        Args: { p_deployment: string }
        Returns: string
      }
      guideline_section_defaults: {
        Args: never
        Returns: {
          content: string
          section_key: string
        }[]
      }
      has_org_capability: {
        Args: { p_capability: string; p_org: string }
        Returns: boolean
      }
      help_content_overrides: {
        Args: never
        Returns: {
          content: string
          prompt_key: string
        }[]
      }
      instruction_kind_for: {
        Args: { p_issue: string; p_run_kind: string }
        Returns: string
      }
      is_active_org_member: { Args: { org: string }; Returns: boolean }
      is_approved_user: { Args: never; Returns: boolean }
      is_interactive_placement_legal: {
        Args: { p_worker_id: string }
        Returns: boolean
      }
      is_org_member: { Args: { org: string }; Returns: boolean }
      is_org_member_text: { Args: { org: string }; Returns: boolean }
      is_org_owner: { Args: { org: string }; Returns: boolean }
      is_own_principal: { Args: { p_principal: string }; Returns: boolean }
      is_platform_admin: { Args: never; Returns: boolean }
      issue_attempt_count: { Args: { p_issue: string }; Returns: number }
      issue_dispatch_block: {
        Args: { p_issue: string; p_kind?: string }
        Returns: {
          hard: boolean
          reason: string
        }[]
      }
      issue_dispatch_refusal: {
        Args: { p_issue: string; p_kind: string }
        Returns: string
      }
      issue_hold_reason: {
        Args: { p_issue: string; p_kind: string }
        Returns: string
      }
      issue_in_trouble: { Args: { p_issue: string }; Returns: boolean }
      list_agent_failures: {
        Args: { p_limit?: number }
        Returns: {
          category: string
          created_at: string
          detail: Json
          error: string
          id: number
          issue_id: string
          issue_title: string
          issue_type: string
          kind: string
          org_id: string
          org_name: string
          preset_name: string
          preset_version: number
          project_id: string
          project_name: string
          resumable: boolean
          run_exists: boolean
          run_id: string
          status: string
          worker_id: string
          worker_name: string
          worker_type: string
        }[]
      }
      next_org_shortname: { Args: { p_name: string }; Returns: string }
      next_project_slug: {
        Args: { p_name: string; p_org: string }
        Returns: string
      }
      next_release_version: { Args: { p_project: string }; Returns: string }
      org_issue_dispatch_blocks: {
        Args: { p_org: string }
        Returns: {
          hard: boolean
          issue_id: string
          reason: string
        }[]
      }
      org_pending_count: { Args: { p_org: string }; Returns: number }
      org_project_spend: {
        Args: { p_org: string }
        Returns: {
          project_id: string
          spent_usd: number
          unmeasured_calls: number
        }[]
      }
      org_queue_hold_reasons: {
        Args: { p_org: string }
        Returns: {
          reason: string
          run_id: string
        }[]
      }
      preview_issue_instructions: {
        Args: { p_issue: string; p_kind?: string }
        Returns: Json
      }
      project_environment_md: { Args: { p_project: string }; Returns: string }
      project_spend_usd: { Args: { p_project: string }; Returns: number }
      promote_app_issue: {
        Args: { p_app_issue: string; p_epic_id?: string }
        Returns: string
      }
      prompt_template_override: { Args: { p_key: string }; Returns: string }
      record_github_app_installation: {
        Args: {
          p_account_login: string
          p_account_type: string
          p_connected_by: string
          p_installation_id: number
          p_org: string
        }
        Returns: undefined
      }
      record_run_activity: {
        Args: { p_coalesce_seconds?: number; p_run: string; p_tool: string }
        Returns: boolean
      }
      record_run_trace: {
        Args: {
          p_content: string
          p_kind: string
          p_run: string
          p_worker: string
        }
        Returns: number
      }
      regenerate_worker_token: { Args: { p_worker: string }; Returns: string }
      reject_run: {
        Args: { p_comment: string; p_run: string }
        Returns: undefined
      }
      release_signoff_blocker: { Args: { p_release: string }; Returns: string }
      reorder_factory_queue: {
        Args: { p_project: string; p_run_ids: string[] }
        Returns: undefined
      }
      reveal_deployment_report_key: {
        Args: { p_deployment: string }
        Returns: string
      }
      reveal_worker_token: { Args: { p_worker: string }; Returns: string }
      rollup_run_usage: { Args: { p_run: string }; Returns: undefined }
      run_coverage: {
        Args: { p_run: string }
        Returns: {
          commit_count: number
          issue_id: string
          landed_at: string
          landed_sha: string
          ordinal: number
        }[]
      }
      run_hold_reason: { Args: { p_run: string }; Returns: string }
      run_issue_ids: {
        Args: { p_run: string }
        Returns: {
          issue_id: string
          ordinal: number
        }[]
      }
      run_work_units: { Args: { p_run: string }; Returns: number }
      seed_issue_instructions: {
        Args: { p_issue: string; p_kind: string }
        Returns: undefined
      }
      seed_org_default_project_template: {
        Args: { p_org: string }
        Returns: string
      }
      seed_org_presets: { Args: { p_org: string }; Returns: number }
      set_claude_subscription_token: {
        Args: { p_org: string; p_token: string }
        Returns: undefined
      }
      set_llm_provider_key: {
        Args: { p_key: string; p_provider: string }
        Returns: undefined
      }
      set_mcp_server_key: {
        Args: { p_key: string; p_server: string }
        Returns: undefined
      }
      set_platform_llm_key: { Args: { p_key: string }; Returns: undefined }
      set_run_paused: {
        Args: { p_paused: boolean; p_run: string }
        Returns: undefined
      }
      set_worker_project: {
        Args: { p_project: string; p_worker: string }
        Returns: undefined
      }
      shares_org_with_caller: {
        Args: { p_principal: string }
        Returns: boolean
      }
      shares_org_with_caller_user: {
        Args: { p_user: string }
        Returns: boolean
      }
      slugify: { Args: { input: string; max_len?: number }; Returns: string }
      start_new_epic: {
        Args: { p_project: string }
        Returns: {
          active: boolean
          completed_at: string | null
          completed_by: string | null
          created_at: string
          created_by: string | null
          description: string | null
          id: string
          number: number
          org_id: string
          project_id: string
          status: string
          title: string
          updated_at: string
        }
        SetofOptions: {
          from: "*"
          to: "epics"
          isOneToOne: true
          isSetofReturn: false
        }
      }
      worker_attempt_count: {
        Args: { p_issue: string; p_worker: string }
        Returns: number
      }
      worker_exhausted_on_issue: {
        Args: { p_issue: string; p_worker: string }
        Returns: boolean
      }
      worker_has_grant: {
        Args: { p_capability: string; p_project: string; p_worker: string }
        Returns: boolean
      }
      worker_instruction_for: {
        Args: { p_kind: string; p_project: string }
        Returns: string
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const
