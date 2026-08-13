import { DirectedGraph } from 'graphology';
import type { FSWatcher } from 'chokidar';

export interface CipherState {
  graph: DirectedGraph | null;
  projectRoot: string | null;
  projectName: string | null;
  watcher: FSWatcher | null;
}

export function createEmptyState(): CipherState {
  return {
    graph: null,
    projectRoot: null,
    projectName: null,
    watcher: null,
  };
}

export function isProjectLoaded(state: CipherState): boolean {
  return state.graph !== null && state.projectRoot !== null;
}
