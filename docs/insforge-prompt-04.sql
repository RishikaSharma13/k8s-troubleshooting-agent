CREATE TABLE IF NOT EXISTS public.investigations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  root_cause TEXT NOT NULL,
  namespace TEXT,
  confidence INTEGER NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
  status TEXT NOT NULL DEFAULT 'completed'
);

ALTER TABLE public.investigations ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT ON public.investigations TO authenticated;
DROP POLICY IF EXISTS investigations_select_own ON public.investigations;
CREATE POLICY investigations_select_own ON public.investigations FOR SELECT TO authenticated USING (user_id = auth.uid());
DROP POLICY IF EXISTS investigations_insert_own ON public.investigations;
CREATE POLICY investigations_insert_own ON public.investigations FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());
