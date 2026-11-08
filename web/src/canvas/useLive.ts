import { useCallback, useEffect, useRef, useState } from "react";
import { liveUrl } from "../api/client";

// The memo's room over a websocket: presence, one person per box, drafts, and edits through the door.
// On a dropped connection it reconnects and asks the page to load the memo again (nothing is missed).

export type LiveMessage =
  | { type: "people"; people: { email: string; editing: string | null }[] }
  | { type: "box_taken"; element_id: string; by: string }
  | { type: "draft"; by: string; element_id: string; text: string }
  | {
      type: "operation";
      by: string;
      op: string;
      target: string;
      client_op_id?: string;
      status: "applied" | "refused" | "kept_aside";
      reason: string | null;
      sequence: number;
      element: unknown;
      slides: unknown;
      created: string[];
    }
  | { type: "reload"; by: string; reason: string };

export function useLive(memoId: string, onMessage: (m: LiveMessage) => void, onReconnect: () => void) {
  const socket = useRef<WebSocket | null>(null);
  const handler = useRef(onMessage);
  const reconnect = useRef(onReconnect);
  handler.current = onMessage;
  reconnect.current = onReconnect;
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let closed = false;
    let retry = 0;
    let timer: number | undefined;
    const open = (again: boolean) => {
      const ws = new WebSocket(liveUrl(memoId));
      socket.current = ws;
      ws.onopen = () => {
        setConnected(true);
        retry = 0;
        if (again) reconnect.current();
      };
      ws.onmessage = (e) => handler.current(JSON.parse(e.data) as LiveMessage);
      ws.onclose = (e) => {
        setConnected(false);
        if (closed || e.code === 4403) return;
        timer = window.setTimeout(() => open(true), Math.min(500 * 2 ** retry++, 8000));
      };
    };
    open(false);
    return () => {
      closed = true;
      window.clearTimeout(timer);
      socket.current?.close();
    };
  }, [memoId]);

  const send = useCallback((message: object) => {
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify(message));
      return true;
    }
    return false;
  }, []);

  return { connected, send };
}
