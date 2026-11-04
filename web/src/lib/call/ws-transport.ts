import type { Transport } from "./controller";

/** The one call WebSocket. Sends queue until the socket opens. */
export class WebSocketTransport implements Transport {
  onMessage: (data: string | ArrayBuffer) => void = () => {};
  onClose: (reason: string) => void = () => {};
  private readonly ws: WebSocket;
  private queue: Array<string | ArrayBuffer> = [];

  constructor(url: string) {
    this.ws = new WebSocket(url);
    this.ws.binaryType = "arraybuffer";
    this.ws.onopen = () => {
      for (const d of this.queue) this.ws.send(d);
      this.queue = [];
    };
    this.ws.onmessage = (e: MessageEvent<string | ArrayBuffer>) => this.onMessage(e.data);
    this.ws.onclose = (e) => this.onClose(e.code === 1000 ? "closed" : `socket_closed_${e.code}`);
  }

  send(data: string | ArrayBuffer): void {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(data);
    else if (this.ws.readyState === WebSocket.CONNECTING) this.queue.push(data);
  }

  close(): void {
    this.ws.close(1000);
  }
}
