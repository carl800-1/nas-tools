export interface Process {
  id: string;
  name: string;
  description: string;
  tools: string[];
}

export interface Industry {
  id: string;
  name: string;
  description: string;
  icon: string;
  processes: Process[];
  color: string;
}

export interface Tool {
  id: string;
  name: string;
  category: string;
  isCordless: boolean;
  description: string;
  image: string;
  applications: string[];
}
