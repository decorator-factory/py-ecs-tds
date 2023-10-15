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

class Debouncer<T> {
    private lastMs: number
    private lastValue: T | null
    private timer: number | null

    constructor(
        private intervalMs: number,
        private doSend: (value: T) => void,
    ) {
        this.lastMs = new Date().getTime()
        this.timer = null
        this.lastValue = null
    }

    public changeInterval(newIntervalMs: number) {
        this.intervalMs = newIntervalMs
    }

    public send(value: T) {
        const now = new Date().getTime()
        const timeLeftMs = this.intervalMs - (now - this.lastMs)
        if (timeLeftMs <= 0) {
            this.doSend(value)
            this.lastMs = now
        } else {
            this.lastValue = value
            if (this.timer === null) {
                this.timer = setTimeout(() => {
                    this.doSend(this.lastValue!)
                }, timeLeftMs)
            }
        }
    }
}

class Game {
    private players: Map<number, Player>
    private bullets: Map<
        number,
        { x: number; y: number; isSupercharged: boolean }
    >
    private boxes: Box[]
    private circles: Circle[]
    private notifications: Notification[]
    private myId: number | null
    private rotateDebouncer: Debouncer<number>
    private clientAngle: number = 0

    constructor(
        private ws: WebSocket,
        private username: string,
        private canvas: HTMLCanvasElement,
    ) {
        this.players = new Map()
        this.bullets = new Map()
        this.boxes = []
        this.circles = []
        this.notifications = []
        this.myId = null
        this.rotateDebouncer = new Debouncer(200, (radians: number) => {
            this.sendMessage({ type: "rotate", radians })
        })
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
            player.angle = msg.angle
        } else if (msg.type === "player_health_changed") {
            const player = this.players.get(msg.id)
            if (!player) {
                console.error(`Player with ID ${msg.id} not found!`)
                return
            }
            player.health = msg.new_health
        } else if (msg.type === "player_score_changed") {
            const player = this.players.get(msg.id)
            if (!player) {
                console.error(`Player with ID ${msg.id} not found!`)
                return
            }
            player.score = msg.new_score
        } else if (msg.type === "world_snapshot") {
            for (const player of msg.players) {
                this.players.set(player.id, {
                    id: player.id,
                    username: player.username,
                    x: player.x,
                    y: player.y,
                    angle: player.angle,
                    health: player.health,
                    score: player.score,
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
                angle: 0,
                health: 0,
                score: 0,
            })
            this.notifications.push({
                message: `${msg.username} joined`,
                ttl: 6,
            })
        } else if (msg.type === "player_died") {
            const player = this.players.get(msg.id)
            if (!player) return
            this.notifications.push({
                message: `${player.username} died`,
                ttl: 6,
            })
            this.players.delete(msg.id)
        } else if (msg.type === "player_left") {
            const player = this.players.get(msg.id)
            if (!player) return
            this.notifications.push({
                message: `${player.username} left`,
                ttl: 6,
            })
            this.players.delete(msg.id)
        } else if (msg.type === "bullet_position") {
            this.bullets.set(msg.id, {
                x: msg.x,
                y: msg.y,
                isSupercharged: msg.is_supercharged,
            })
        } else if (msg.type === "bullet_gone") {
            this.bullets.delete(msg.id)
        } else if (msg.type === "bad_message") {
            console.error("We sent a bad message:", msg.error)
        }
    }

    private myPlayer() {
        if (this.myId === null) {
            return null
        } else {
            return this.players.get(this.myId) || null
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
        }

        this.canvas.addEventListener("mousedown", (e) => {
            this.sendMessage({ type: "rotate", radians: this.clientAngle })
            this.rotateDebouncer.changeInterval(25)
            this.sendMessage({ type: "input_down", control: "fire" })
        })

        this.canvas.addEventListener("mouseup", (e) => {
            this.rotateDebouncer.changeInterval(200)
            this.sendMessage({ type: "input_up", control: "fire" })
        })

        this.canvas.addEventListener("mousemove", (e) => {
            const me = this.myPlayer()
            if (me === null) return

            const rect = this.canvas.getBoundingClientRect()
            const mouseX = e.clientX - rect.left
            const mouseY = e.clientY - rect.top
            const radians = Math.atan2(mouseY - me.y, mouseX - me.x)
            this.clientAngle = radians
            this.rotateDebouncer.send(radians)
        })

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

        for (const { x, y, isSupercharged } of this.bullets.values()) {
            if (isSupercharged) {
                ctx.beginPath()
                ctx.fillStyle = "rgba(255, 119, 56)"
                ctx.arc(x, y, 6, 0, Math.PI * 2)
                ctx.fill()

                ctx.beginPath()
                ctx.fillStyle = "rgb(217, 57, 33)"
                ctx.arc(x, y, 4, 0, Math.PI * 2)
                ctx.fill()
            } else {
                ctx.beginPath()
                ctx.fillStyle = "black"
                ctx.arc(x, y, 4, 0, Math.PI * 2)
                ctx.fill()
            }
        }

        ctx.fillStyle = "#2e80db"
        for (const { x, y, width, height } of this.boxes) {
            ctx.fillRect(x, y, width, height)
        }

        ctx.fillStyle = "#2e80db"
        for (const { x, y, radius } of this.circles) {
            ctx.beginPath()
            ctx.arc(x, y, radius, 0, Math.PI * 2)
            ctx.fill()
        }

        for (const { id, x, y, angle } of this.players.values()) {
            const displayAngle = id === this.myId ? this.clientAngle : angle

            // body
            ctx.fillStyle = "red"
            ctx.beginPath()
            ctx.arc(x, y, 16, 0, Math.PI * 2)
            ctx.fill()

            // eyes
            const deltaAngle = (30 * Math.PI) / 180
            for (const a of [
                displayAngle - deltaAngle,
                displayAngle + deltaAngle,
            ]) {
                ctx.beginPath()
                ctx.fillStyle = "white"
                ctx.arc(
                    x + Math.cos(a) * 11,
                    y + Math.sin(a) * 11,
                    4,
                    0,
                    Math.PI * 2,
                )
                ctx.fill()

                ctx.beginPath()
                ctx.fillStyle = "black"
                ctx.arc(
                    x + Math.cos(a) * 12,
                    y + Math.sin(a) * 12,
                    2,
                    0,
                    Math.PI * 2,
                )
                ctx.fill()
            }
        }

        for (const { x, y, username, id, health } of this.players.values()) {
            ctx.fillStyle = "black"
            ctx.font = "12pt sans-serif"
            ctx.textAlign = "center"
            ctx.fillText(`#${id} ${username} (${health} HP)`, x, y - 20)
        }

        this.notifications.forEach((notif) => {
            notif.ttl -= delta
        })
        this.notifications = this.notifications.filter(({ ttl }) => ttl >= 0)

        const notifWidth = 230
        const notifHeight = 20
        ctx.textAlign = "left"
        ctx.font = "14pt sans-serif"
        for (const [i, { message, ttl }] of this.notifications.entries()) {
            const opacity = Math.min(ttl, 1.0)
            const baseX = this.canvas.width - notifWidth
            const baseY = this.canvas.height - notifHeight * i - notifHeight
            ctx.fillStyle = `rgb(0, 0, 0, ${opacity * 0.7})`
            ctx.fillRect(baseX, baseY, notifWidth, notifHeight)
            ctx.fillStyle = `rgb(255, 255, 255, ${opacity})`
            ctx.fillText(
                message,
                baseX + 4,
                baseY + notifHeight - 2,
                notifWidth - 4,
            )
        }

        // scoreboard
        const scoreboardPlayers = [...this.players.values()]
            .sort(keyToCmp(({ id }) => id))
            .sort(keyToCmp(({ score }) => -score))
            .slice(0, 5)
        const scoreWidth = 230
        const scoreHeight = 20
        ctx.textAlign = "left"
        ctx.font = "14pt sans-serif"
        for (const [
            i,
            { id, username, score },
        ] of scoreboardPlayers.entries()) {
            const baseX = this.canvas.width - scoreWidth
            const baseY = scoreHeight * i
            ctx.fillStyle = "rgb(0, 0, 0, 0.4)"
            ctx.fillRect(baseX, baseY, scoreWidth, scoreHeight)
            ctx.fillStyle = "white"
            ctx.fillText(
                `#${id} ${username.padEnd(20, " ")} ${score}`,
                baseX + 4,
                baseY + scoreHeight - 4,
                scoreWidth - 4,
            )
        }
    }
}

type KeyFunc<T> = ((t: T) => string) | ((t: T) => number)

const keyToCmp =
    <T>(key: KeyFunc<T>) =>
    (a: T, b: T) => {
        const ka = key(a)
        const kb = key(b)
        if (ka < kb) return -1
        else if (ka > kb) return 1
        else return 0
    }

type Player = {
    id: number
    username: string
    x: number
    y: number
    angle: number
    health: number
    score: number
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
        angle: number
        health: number
        score: number
    }
    export type ShapeIntro =
        | { kind: "box"; x: number; y: number; width: number; height: number }
        | { kind: "circle"; x: number; y: number; radius: number }

    export type ServerMessage =
        | { type: "welcome"; client_id: number }
        | { type: "goodbye" }
        | { type: "player_joined"; id: number; username: string }
        | { type: "player_left"; id: number }
        | { type: "player_died"; id: number }
        | {
              type: "player_position"
              id: number
              x: number
              y: number
              angle: number
          }
        | {
              type: "player_health_changed"
              id: number
              new_health: number
          }
        | {
              type: "player_score_changed"
              id: number
              new_score: number
          }
        | {
              type: "bullet_position"
              id: number
              x: number
              y: number
              is_supercharged: boolean
          }
        | { type: "bullet_gone"; id: number }
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
        | { type: "rotate"; radians: number }
}
