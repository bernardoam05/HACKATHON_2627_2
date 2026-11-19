// Owner: Team B. Room CRUD incl. isEntrance flag (a contact sensor in an entrance room is the front door, D14).
import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { Room } from "../../api/types";
import { useDevices } from "../../live/DevicesProvider";
import { useRooms } from "../../live/RoomsProvider";
import { errorMessage } from "../../lib/useResource";
import { Badge, Button, Card, Empty, ErrorNote, Field, inputClass, Loading, normalize } from "../../ui";

export default function RoomEditor() {
  const { rooms, loading, error, reload } = useRooms();
  const { devices } = useDevices();
  const count = (id: string) => devices.filter((d) => d.roomId === id).length;

  return (
    <Card title="Divisões">
      <AddRoom />
      {error ? <ErrorNote message={error} onRetry={reload} />
        : loading && rooms.length === 0 ? <Loading />
        : rooms.length === 0 ? <Empty>Ainda não há divisões. Cria a primeira acima.</Empty>
        : (
          <ul className="m-0 grid list-none gap-2 p-0">
            {rooms.map((r) => <li key={r.id}><RoomRow room={r} devices={count(r.id)} /></li>)}
          </ul>
        )}
    </Card>
  );
}

function RoomRow({ room, devices }: { room: Room; devices: number }) {
  const { remove } = useRooms();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const run = async () => {
    const extra = devices > 0 ? ` Os ${devices} dispositivos ficam sem divisão.` : "";
    if (!window.confirm(`Apagar a divisão "${room.name}"?${extra}`)) return;
    setBusy(true);
    setError(undefined);
    try {
      await api.deleteRoom(room.id);
      remove(room.id);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-2 rounded-lg border border-line bg-canvas px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{room.name}</span>
          {room.isEntrance && <Badge tone="accent">Entrada</Badge>}
          <span className="text-xs text-muted">
            {devices} {devices === 1 ? "dispositivo" : "dispositivos"}
          </span>
        </div>
        <Button variant="ghost" disabled={busy} onClick={run}>Apagar</Button>
      </div>
      {error && <ErrorNote message={error} />}
    </div>
  );
}

function AddRoom() {
  const { add, rooms } = useRooms();
  const [name, setName] = useState("");
  const [isEntrance, setIsEntrance] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const duplicate = Boolean(name.trim()) && rooms.some((r) => normalize(r.name) === normalize(name));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim() || duplicate) return;
    setBusy(true);
    setError(undefined);
    try {
      add(await api.createRoom({ name: name.trim(), isEntrance }));
      setName("");
      setIsEntrance(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mb-4 grid gap-2">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-48 flex-1">
          <Field label="Nova divisão" error={duplicate && "Já existe uma divisão com este nome."}>
            <input className={inputClass} value={name} placeholder="ex. Cozinha"
                   onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>
        <Button type="submit" variant="primary" disabled={busy || !name.trim() || duplicate}>Criar</Button>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="size-4 accent-accent" checked={isEntrance}
               onChange={(e) => setIsEntrance(e.target.checked)} />
        É a entrada da casa
        <span className="text-muted">(o sensor da porta aqui indica quando alguém sai)</span>
      </label>
      {error && <ErrorNote message={error} />}
    </form>
  );
}
