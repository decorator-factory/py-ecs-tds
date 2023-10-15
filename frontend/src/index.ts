// Entry point

export function main({
    input,
    button,
    canvas,
}: {
    input: HTMLInputElement
    button: HTMLButtonElement
    canvas: HTMLCanvasElement
}) {
    button.addEventListener("click", () => {
        const username = input.value.trim()
        if (!username) {
            return
        }
        button.parentElement!.removeChild(button)
        input.parentElement!.removeChild(input)

        canvas.width = 1200
        canvas.height = 600

        const ws = connectToWs()
        const game = new Game(ws, username, canvas)
        game.beginRender()
    })
}

const connectToWs = (): WebSocket => {
    const url = new URL("/api/ws", window.location.href)
    url.protocol = url.protocol.replace("http", "ws")
    url.protocol = url.protocol.replace("https", "wss")
    return new WebSocket(url)
}

type Notification = {
    message: string
    ttl: number
}

class Game {
    private players: Map<number, Player>
    private boxes: Box[]
    private circles: Circle[]
    private notifications: Notification[]
    private myId: number | null

    constructor(
        private ws: WebSocket,
        private username: string,
        private canvas: HTMLCanvasElement,
    ) {
        this.players = new Map()
        this.boxes = []
        this.circles = []
        this.notifications = []
        this.myId = null
        ws.addEventListener("open", () => this.beginUpdate())
        ws.addEventListener("message", (e) => {
            const msg: schema.ServerMessage = JSON.parse(e.data)
            this.processMessage(msg)
        })
    }

    private sendMessage(msg: schema.ClientMessage) {
        this.ws.send(JSON.stringify(msg))
    }

    private processMessage(msg: schema.ServerMessage) {
        if (msg.type == "welcome") {
            this.myId = msg.client_id
        } else if (msg.type === "player_position") {
            const player = this.players.get(msg.id)
            if (!player) {
                console.error(`Player with ID ${msg.id} not found!`)
                return
            }
            player.x = msg.x
            player.y = msg.y
        } else if (msg.type === "world_snapshot") {
            for (const player of msg.players) {
                this.players.set(player.id, {
                    id: player.id,
                    username: player.username,
                    x: player.x,
                    y: player.y,
                })
            }
            for (const shape of msg.shapes) {
                if (shape.kind === "box") {
                    this.boxes.push(shape)
                } else {
                    this.circles.push(shape)
                }
            }
        } else if (msg.type === "player_joined") {
            this.players.set(msg.id, {
                id: msg.id,
                username: msg.username,
                x: 0,
                y: 0,
            })
            this.notifications.push({
                message: `${msg.username} joined`,
                ttl: 10,
            })
        } else if (msg.type === "player_left") {
            const { username } = this.players.get(msg.id)!
            this.notifications.push({ message: `${username} left`, ttl: 10 })
            this.players.delete(msg.id)
        } else if (msg.type === "bad_message") {
            console.error("We sent a bad message:", msg.error)
        }
    }

    beginUpdate() {
        const controls = new Set<schema.Control>()

        this.sendMessage({ type: "hello", username: this.username })

        const keyToControl: Record<string, schema.Control> = {
            ArrowLeft: "left",
            ArrowRight: "right",
            ArrowDown: "down",
            ArrowUp: "up",
            " ": "fire",
        }

        window.addEventListener("keydown", (e) => {
            const control = keyToControl[e.key]
            if (control && !controls.has(control)) {
                controls.add(control)
                this.sendMessage({ type: "input_down", control })
            }
        })

        window.addEventListener("keyup", (e) => {
            const control = keyToControl[e.key]
            if (control && controls.has(control)) {
                controls.delete(control)
                this.sendMessage({ type: "input_up", control })
            }
        })
    }

    beginRender() {
        let lastTimestamp: number | null = null

        const onFrameRender = (timestamp: DOMHighResTimeStamp) => {
            if (lastTimestamp === null) {
                lastTimestamp = timestamp
            } else {
                const delta = (timestamp - lastTimestamp) / 1000
                lastTimestamp = timestamp
                this.renderFrame(delta)
            }
            window.requestAnimationFrame(onFrameRender)
        }

        window.requestAnimationFrame(onFrameRender)
    }

    private renderFrame(delta: number) {
        const ctx = this.canvas.getContext("2d")!
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height)

        ctx.fillStyle = "cyan"
        for (const { x, y, width, height } of this.boxes) {
            ctx.fillRect(x, y, width, height)
        }

        ctx.fillStyle = "cyan"
        ctx.beginPath()
        for (const { x, y, radius } of this.circles) {
            ctx.arc(x, y, radius, 0, Math.PI * 2)
        }
        ctx.closePath()
        ctx.fill()

        ctx.fillStyle = "red"
        for (const { x, y } of this.players.values()) {
            ctx.beginPath()
            ctx.arc(x, y, 20, 0, Math.PI * 2)
            ctx.fill()
        }

        for (const { x, y, username, id } of this.players.values()) {
            ctx.fillStyle = "black"
            ctx.font = "12pt sans-serif"
            ctx.textAlign = "center"
            ctx.fillText(`${username}(${id})`, x, y - 20)
        }

        this.notifications.forEach((notif) => {
            notif.ttl -= delta
        })
        this.notifications = this.notifications.filter(({ ttl }) => ttl >= 0)

        const notifWidth = 230
        const notifHeight = 20

        for (const [i, { message, ttl }] of this.notifications.entries()) {
            const opacity = Math.min(ttl, 1.0)

            const baseX = this.canvas.width - notifWidth
            const baseY = this.canvas.height - notifHeight * i - notifHeight
            ctx.fillStyle = `rgb(0, 0, 0, ${opacity * 0.7})`
            ctx.fillRect(baseX, baseY, notifWidth, notifHeight)
            ctx.fillStyle = `rgb(255, 255, 255, ${opacity})`
            ctx.textAlign = "left"
            ctx.font = "14pt sans-serif"
            ctx.fillText(
                message,
                baseX + 4,
                baseY + notifHeight - 2,
                notifWidth - 4,
            )
        }
    }
}

type Player = {
    id: number
    username: string
    x: number
    y: number
}

type Box = {
    x: number
    y: number
    width: number
    height: number
}

type Circle = {
    x: number
    y: number
    radius: number
}

namespace schema {
    export type PlayerIntro = {
        id: number
        username: string
        x: number
        y: number
    }
    export type ShapeIntro =
        | { kind: "box"; x: number; y: number; width: number; height: number }
        | { kind: "circle"; x: number; y: number; radius: number }

    export type ServerMessage =
        | { type: "welcome"; client_id: number }
        | { type: "goodbye" }
        | { type: "player_joined"; id: number; username: string }
        | { type: "player_left"; id: number }
        | { type: "player_position"; id: number; x: number; y: number }
        | {
              type: "world_snapshot"
              players: PlayerIntro[]
              shapes: ShapeIntro[]
          }
        | { type: "bad_message"; error: unknown }

    export type Control = "left" | "right" | "up" | "down" | "fire"

    export type ClientMessage =
        | { type: "hello"; username: string }
        | { type: "input_down"; control: Control }
        | { type: "input_up"; control: Control }
}
