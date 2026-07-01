import { useEffect, useState } from "react";
import { api } from "../api";

export default function SkillMdPreview() {
  const [md, setMd] = useState<string>("");
  const [err, setErr] = useState<string>("");

  useEffect(() => {
    api.skillMd().then((r) => setMd(r.markdown)).catch((e) => setErr(e.message || String(e)));
  }, []);

  if (err) return <div className="notice err">{err}</div>;
  return <pre className="skill">{md || "Loading skill.md…"}</pre>;
}
